#!/usr/bin/env python

from mongoengine import connect

from pycoshark.mongomodels import Project, VCSSystem, File, Commit, FileAction, Issue, IssueSystem
from pycoshark.utils import create_mongodb_uri_string, git_tag_filter, get_affected_versions, java_filename_filter, jira_is_resolved_and_fixed

from util.git import CollectGit


class InducingMiner:
    """Mine inducing commits with the help of CollectGit and blame."""

    def __init__(self, logger, database, user, password, host, port, authentication, ssl, project_name, vcs_url, repo_path):
        self._log = logger
        self._repo_path = repo_path
        self._project_name = project_name

        uri = create_mongodb_uri_string(user, password, host, port, authentication, ssl)
        connect(database, host=uri)

        pr = Project.objects.get(name=project_name)
        vcs = VCSSystem.objects.get(project_id=pr.id, url=vcs_url)
        its = IssueSystem.objects.get(project_id=pr.id)

        self._vcs_id = vcs.id
        self._its_id = its.id
        self._jira_key = its.url.split('project=')[-1]

    def collect(self):
        """Collect inducing commits and write them to the database."""
        self._cg = CollectGit(self._repo_path)
        self._cg.collect()

        self._version_dates = self._collect_version_dates()
        self._clear_inducing()

    def _clear_inducing(self):
        """Delete all inducing information from the dtabse for the chosen project."""
        self._log.info('setting all FileAction.induces to []')
        for c in Commit.objects.filter(vcs_system_id=self._vcs_id).only('id'):
            for fa in FileAction.objects.filter(commit_id=c.id):
                fa.induces = []  # this deletes everything, including previous runs with a different label
                fa.save()

    def _find_boundary_date(self, issue_ids, version_dates, affected_versions):
        """Find suspect boundary date.

        latest issue information but earliest date in commit (between created_at and affected versions)

        - latest creation date of linked bugs
        - earliest affected version
        """
        issue_dates = []
        affected_version_dates = []
        for issue_id in issue_ids:
            issue = Issue.objects.get(id=issue_id)

            if not jira_is_resolved_and_fixed(issue):
                continue

            if not issue.created_at:
                self._log.warn('no reporting date for {} id({}), ignoring it'.format(issue.external_id, issue.id))
                continue

            for av in issue.affects_versions:
                avt = tuple(av.split('.'))
                if avt in version_dates.keys():

                    for version_date in version_dates[avt]:
                        if version_date not in affected_version_dates:
                            affected_version_dates.append(version_date)
                else:
                    self._log.warn('affected version {} not found'.format(avt))

            issue_dates.append(issue.created_at)

        # find latest bug report
        suspect_boundary_date = max(issue_dates)

        # latest bug report
        self._log.debug('latest bug report date is {} of {}'.format(suspect_boundary_date, issue_dates))

        # return earliest affected version, only if we want
        if affected_versions and affected_version_dates:
            min_affected_date = min(affected_version_dates)
            self._log.debug('affected versions earliest date is {} while max bug report date is {}'.format(min_affected_date, suspect_boundary_date))
            suspect_boundary_date = min(min_affected_date, suspect_boundary_date)

        return suspect_boundary_date

    def _collect_version_dates(self):
        """Match affected versions from the ITS to tag names from the VCS.

        3.0.0 from ITS matches 3.0.0 from VCS
        3.0 from ITS matches all of 3.0.X from VCS
        """
        tags = git_tag_filter(self._project_name, discard_patch=False, discard_broken_dates=True)

        # collect tags and their version and date used in this VCS system
        tag_versions = {}
        for t in tags:
            c = Commit.objects.get(vcs_system_id=self._vcs_id, revision_hash=t['revision'])
            tag_versions[tuple([str(tv) for tv in t['version']])] = c.committer_date

        # collect affected versions used in this ITS
        affected_versions = set()
        for i in Issue.objects.filter(issue_system_id=self._its_id):
            for av in get_affected_versions(i, self._project_name, self._jira_key):
                affected_versions.add(tuple(av))

        # map affected versions to possible dates
        version_dates = {}
        for av in affected_versions:
            for tv, dt in tag_versions.items():
                startswith = all(tv1 == av1 for tv1, av1 in zip(tv, av))  # check if evey item of the tuple matches
                if not startswith:
                    continue
                if av not in version_dates.keys():
                    version_dates[av] = []

                version_dates[av].append(dt)
        return version_dates

    def write_bug_inducing(self, label='validated_bugfix', inducing_strategy='code_only', java_only=True, affected_versions=False, name=None):
        """Write bug inducing information into FileAction.

        1. get all commits that are bug-fixing
        2. run blame for all files for all deleted lines in bug-fixing commits to find bug-inducing file actions and commits
        3. save to mongo_db
        """
        params = {
            'vcs_system_id': self._vcs_id,
            'labels__{}'.format(label): True,
            'parents__1__exists': False,
        }

        if label == 'validated_bugfix':
            params['fixed_issue_ids__0__exists'] = True
        elif label == 'adjustedszz_bugfix':
            params['szz_issue_ids__0__exists'] = True
        else:
            raise Exception('unknown label')

        all_changes = {}
        for bugfix_commit in Commit.objects.filter(**params).only('revision_hash', 'id', 'fixed_issue_ids', 'szz_issue_ids', 'committer_date').timeout(False):

            # only modified files
            for fa in FileAction.objects.filter(commit_id=bugfix_commit.id, mode='M'):
                f = File.objects.get(id=fa.file_id)

                # only java files
                if java_only and not java_filename_filter(f.path.lower()):
                    continue

                if label == 'validated_bugfix':
                    suspect_boundary_date = self._find_boundary_date(bugfix_commit.fixed_issue_ids, self._version_dates, affected_versions)
                elif label == 'adjustedszz_bugfix':
                    suspect_boundary_date = self._find_boundary_date(bugfix_commit.szz_issue_ids, self._version_dates, affected_versions)
                else:
                    raise Exception('unknown label')

                # find bug inducing commits, add to our list for this commit and file
                for blame_commit, original_file in self._cg.blame(bugfix_commit.revision_hash, f.path, inducing_strategy):
                    blame_c = Commit.objects.get(vcs_system_id=self._vcs_id, revision_hash=blame_commit)

                    # every commit before our suspect boundary date is counted towards inducing
                    if blame_c.committer_date < suspect_boundary_date:
                        szz_type = 'inducing'

                    # every commit behind our boundary date is counted towards suspects
                    elif blame_c.committer_date >= suspect_boundary_date:
                        szz_type = 'suspect'

                        # if the suspect commit is also a bug-fix it is a partial fix
                        if label in blame_c.labels.keys() and blame_c.labels[label] is True:
                            szz_type = 'partial_fix'

                    self._log.debug('blame commit date {} against boundary date {}, szz_type {}'.format(blame_c.committer_date, suspect_boundary_date, szz_type))
                    for blame_fa in FileAction.objects.filter(commit_id=blame_c.id):
                        blame_f = File.objects.get(id=blame_fa.file_id)

                        if blame_f.path == original_file:
                            key = str(fa.id) + '_' + str(blame_fa.id)

                            if key not in all_changes.keys():
                                all_changes[key] = {'change_file_action_id': fa.id, 'inducing_file_action': blame_fa.id, 'label': label, 'szz_type': szz_type, 'inducing_strategy': inducing_strategy}

        # second run differenciate between hard and weak suspects
        new_types = {}
        self._log.debug('starting second pass for distinguish hard and weak suspects')
        for change, values in all_changes.items():

            # every suspect starts as hard_suspect
            szz_type = 'hard_suspect'
            if values['szz_type'] == 'suspect':

                # is there a fix for this change which is not a suspect (which means it has to be a partial-fix or inducing)
                # we set this type to weak_suspect
                for change2, values2 in all_changes.items():

                    # skip equal
                    if change == change2:
                        continue

                    if values2['inducing_file_action'] == values['inducing_file_action'] and values2['szz_type'] != 'suspect':
                        szz_type = 'weak_suspect'
                        self._log.debug('found another inducing change for this inducing change which is not a suspect, we set szz_type to weak_suspect')
                new_types[change] = szz_type

        # write results
        self._log.debug('writing results')
        for change, values in all_changes.items():
            fa = FileAction.objects.get(id=values['inducing_file_action'])

            szz_type = values['szz_type']
            if szz_type == 'suspect':
                szz_type = new_types[change]

            to_write = {'change_file_action_id': values['change_file_action_id'],
                        'szz_type': szz_type,
                        # these values are defined by the name
                        # 'label': values['label'],
                        # 'inducing_strategy': inducing_strategy,
                        # 'java_only': java_only,
                        # 'affected_versions': affected_versions,
                        'label': name}

            self._log.debug(to_write)
            # we clear everything with this label beforehand because we may re-run this plugin with a different label or strategy
            # new_list = []
            # for d in fa.induces:
            #     if d['label'] != label or d['inducing_strategy'] != inducing_strategy or d['java_only'] != java_only:  # keep values not matching our stuff
            #         new_list.append(d)

            # fa.induces = new_list
            # fa.induces = []  # this deletes everything, also previous runs with a different label

            if to_write not in fa.induces:
                fa.induces.append(to_write)
            fa.save()
