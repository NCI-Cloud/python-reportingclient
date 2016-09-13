#!/usr/bin/python

import sys
import os
import argparse
from reportingclient.client import ReportingClient
from keystoneclient import client as keystone_client
import logging
from csv_utf8 import CSVUTF8DictIterWriter


def get_arg_or_env_var(args, name):
    name = 'os-' + name
    try:
        name_with_hyphens = name.replace('_', '-').lower()
        value = getattr(args, name_with_hyphens)
        args.pop(name_with_hyphens, None)
    except AttributeError:
        # Not supplied in a command-line argument
        name_with_underscores = name.replace('-', '_').upper()
        try:
            value = os.environ[name_with_underscores]
            del os.environ[name_with_underscores]
        except KeyError:
            # Not supplied in environment either
            value = None
    return value


def active_instances(client):
    logger = logging.getLogger(__name__)
    # grab all the required data
    hypervisor = client.fetch('hypervisor')
    instance = client.fetch('instance', active=1)
    project = client.fetch('project')

    # check that every hypervisor has availability_zone defined
    # (since that's what we'll be using to determine AZ for each instance)
    for h in hypervisor:
        if not h['availability_zone']:
            logger.error('No availability_zone for hypervisor %s', h['id'])
            sys.exit(1)
    logger.debug('Checked hypervisor AZ values.')

    # hypervisor 'hostname' field is fully qualified but instance 'hypervisor'
    # field is sometimes not; make a lookup table of hypervisors
    # by "short" (non-fully-qualified) names
    hyp_short = {}
    for h in hypervisor:
        short_name = h['hostname'].split('.')[0]
        if short_name in hyp_short:
            logger.warn(
                'Duplicate short hypervisor names %s (%s and %s).',
                short_name, hyp_short[short_name]['id'], h['id']
            )
            if h['last_seen'] < hyp_short[short_name]['last_seen']:
                # only care about the most recently seen hypervisor
                continue
        hyp_short[short_name] = h

    # check that every instance has a valid hypervisor and project_id defined
    project_by_id = {p['id']: p for p in project}
    instance_hypervisor = {}  # maps instance id to hypervisor object
    instance_by_id = {}  # maps instance id to instance object
    for i in instance:
        if i['hypervisor'] is None:
            logger.warn('Instance %s has no hypervisor; it will be ignored.', i['id'])
            continue
        short_name = i['hypervisor'].split('.')[0]
        if short_name not in hyp_short:
            logger.error('Could not determine hypervisor for instance %s', i['id'])
            sys.exit(1)
        if i['project_id'] not in project_by_id:
            logger.warn('Instance %s has invalid project_id %s; it will be ignored.', i['id'], i['project_id'])
            continue

        instance_hypervisor[i['id']] = hyp_short[short_name]
        instance_by_id[i['id']] = i
    logger.debug('Checked instance hypervisor values.')

    # at this point, sanity checks have been done on all the data;
    # now join data, decorating instance objects with additional fields
    for iid in instance_hypervisor:
        i = instance_by_id[iid]

        # replace availability_zone value with hypervisor's
        old_az = i['availability_zone']  # current version of reporting-pollster sets this unreliably
        new_az = instance_hypervisor[iid]['availability_zone']  # this is more reliable
        i['availability_zone'] = new_az

        # add project display names
        i['project_display_name'] = project_by_id[i['project_id']]['display_name']

    return (instance for instance in instance_by_id.values())


def test_one_report(client, report_name, outfile_name):
    result_iter = (row for row in client.fetch(report_name))
    CSVUTF8DictIterWriter.write_file(result_iter, outfile_name)

def test_all_reports(client, outfile_name):
    for report_name in (report['name'] for report in client.get_reports()):
        test_one_report(client, report_name, outfile_name)

def test_active_instances(client, outfile_name):
    result_iter = active_instances(client)
    CSVUTF8DictIterWriter.write_file(result_iter, outfile_name)

if __name__ == '__main__':
    parser = argparse.ArgumentParser(
        description='Compile list of all active instances.'
    )
    parser.add_argument('--endpoint', required=True, help='reporting-api endpoint')
    parser.add_argument('--output', required=False, help='output path')
    parser.add_argument('--token', default=argparse.SUPPRESS, help='auth token for reporting-api')
    parser.add_argument('--debug', default=False, action='store_true', help='enable debug output (for development)')
    parser.add_argument('--os-username', default=argparse.SUPPRESS, help='Username')
    parser.add_argument('--os-password', default=argparse.SUPPRESS, help="User's password")
    parser.add_argument('--os-auth-url', default=argparse.SUPPRESS, help='Authentication URL')
    parser.add_argument('--os-project-name', default=argparse.SUPPRESS, help='Project name to scope to')
    parser.add_argument('--os-tenant-name', default=argparse.SUPPRESS, help='Project name to scope to')
    args = parser.parse_args()

    if args.debug:
        log_level = logging.DEBUG
    else:
        log_level = logging.WARN
    logging.basicConfig(level=log_level)
    logger = logging.getLogger('reportingclient.client')
    logger.setLevel(log_level)
    vars(args).pop('debug', None)

    outfile_name = vars(args).pop('output', None)

    args.token = get_arg_or_env_var(args, 'token')
    if args.token is None:
        # Attempt to obtain authentication credentials
        username = get_arg_or_env_var(args, 'username')
        password = get_arg_or_env_var(args, 'password')
        project_name = get_arg_or_env_var(args, 'project_name')
        if not project_name:
            project_name = get_arg_or_env_var(args, 'tenant_name')
        auth_url = get_arg_or_env_var(args, 'auth_url')
        if username and password and project_name and auth_url:
            keystone = keystone_client.Client(
                username=username,
                password=password,
                project_name=project_name,
                auth_url=auth_url
            )
            if not keystone.authenticate():
                raise ValueError("Keystone authentication failed")
            args.token = keystone.auth_ref['token']['id']

    client = ReportingClient(**vars(args))
    test_all_reports(client, outfile_name)
    test_active_instances(client, outfile_name)

    sys.exit(0)
