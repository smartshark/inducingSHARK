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

    # everything with label='validated_bugfix' uses commit.fixed_issue_ids
    # szz uses commit.szz_issue_ids
    im.write_bug_inducing(label='adjustedszz_bugfix', inducing_strategy='all', java_only=False, affected_versions=False, ignore_refactorings=False, name='SZZ')  # plain szz
    im.write_bug_inducing(label='issueonly_bugfix', inducing_strategy='code_only', java_only=True, affected_versions=False, ignore_refactorings=True, name='JL+R')  # best automatic szz

    im.write_bug_inducing(label='validated_bugfix', inducing_strategy='all', java_only=False, affected_versions=False, ignore_refactorings=False, name='JLMIV')  # plain szz validated labels
    im.write_bug_inducing(label='validated_bugfix', inducing_strategy='code_only', java_only=True, affected_versions=False, ignore_refactorings=False, name='JLMIV+')  # improved szz validated labels
    im.write_bug_inducing(label='validated_bugfix', inducing_strategy='code_only', java_only=True, affected_versions=True, ignore_refactorings=False, name='JLMIV+AV')  # improved szz validated labels, affected versions

    im.write_bug_inducing(label='validated_bugfix', inducing_strategy='code_only', java_only=True, affected_versions=True, ignore_refactorings=True, name='JLMIV+RAV')  # best + AV

    im.write_bug_inducing(label='validated_bugfix', inducing_strategy='code_only', java_only=True, affected_versions=False, ignore_refactorings=True, name='JLMIV+R')  # improved szz validated labels, without refactorings

    im.write_bug_inducing(label='validated_bugfix', inducing_strategy='code_only', java_only=True, affected_versions=False, ignore_refactorings=False, name='JLMIVLV', only_validated_bugfix_lines=True)  # improved szz validated labels, only validated lines

    end = timeit.default_timer() - start
    log.info("Finished inducingSHARK extraction in {:.5f}s".format(end))


if __name__ == '__main__':
    # we basically re-use the vcsSHARK argparse config here
    parser = get_base_argparser('Analyze the given URI. An URI should be a checked out GIT Repository.', '2.0.1')
    parser.add_argument('-i', '--input', help='Path to the checked out repository directory', required=True)
    parser.add_argument('-pn', '--project-name', help='Hash of the revision.', required=False)
    parser.add_argument('-u', '--repository-url', help='URL of the project (e.g., GIT Url).', required=True)
    parser.add_argument('-ll', '--log-level', help='Log level for stdout (DEBUG, INFO), default INFO', default='INFO')
    main(parser.parse_args())
