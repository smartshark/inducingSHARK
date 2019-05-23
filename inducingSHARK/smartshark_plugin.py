#!/usr/bin/env python

"""Plugin for execution with serverSHARK."""

import sys
import logging
import timeit

from pycoshark.utils import get_base_argparser
from inducing import InducingMiner

# set up logging, we log everything to stdout except for errors which go to stderr
# this is then picked up by serverSHARK
log = logging.getLogger('inducingSHARK')
log.setLevel(logging.INFO)
i = logging.StreamHandler(sys.stdout)
e = logging.StreamHandler(sys.stderr)

i.setLevel(logging.DEBUG)
e.setLevel(logging.ERROR)

log.addHandler(i)
log.addHandler(e)


def main(args):
    if args.log_level:
        log.setLevel(args.log_level)

    # timing
    start = timeit.default_timer()
    log.info("Starting inducingSHARK")

    im = InducingMiner(log, args.db_database, args.db_user, args.db_password, args.db_hostname, args.db_port, args.db_authentication, args.ssl, args.project_name, args.repository_url, args.input)
    im.collect()
    set1 = {'label': 'validated_bugfix', 'inducing_strategy': 'code_only'}
    set2 = {'label': 'adjustedszz_bugfix', 'inducing_strategy': 'all'}

    for kwargs in [set1, set2]:
        for java_only in [True, False]:
            tmp = kwargs
            tmp.update({'java_only': java_only})
            print('running set:', tmp)
            # im.write_bug_inducing(**tmp)

    im.write_bug_inducing(label='adjustedszz_bugfix', inducing_strategy='all', java_only=True)
    im.write_bug_inducing(label='adjustedszz_bugfix', inducing_strategy='all', java_only=False)
    end = timeit.default_timer() - start
    log.info("Finished inducingSHARK extraction in {:.5f}s".format(end))


if __name__ == '__main__':
    # we basically re-use the vcsSHARK argparse config here
    parser = get_base_argparser('Analyze the given URI. An URI should be a checked out GIT Repository.', '2.0.1')
    parser.add_argument('-i', '--input', help='Path to the checked out repository directory', required=True)
    # parser.add_argument('-l', '--label', help='Label to filter changed commits.', required=True)
    # parser.add_argument('-is', '--inducing_strategy', help='Inducing strategy, can be one of: full, code_only', required=True)
    parser.add_argument('-pn', '--project_name', help='Hash of the revision.', required=False)
    parser.add_argument('-u', '--repository_url', help='URL of the project (e.g., GIT Url).', required=True)
    parser.add_argument('-ll', '--log_level', help='Log level for stdout (DEBUG, INFO), default INFO', default='INFO')
    main(parser.parse_args())
