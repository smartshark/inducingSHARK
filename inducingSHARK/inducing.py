#!/usr/bin/env python
import os
import tarfile

from mongoengine import connect
from pympler import asizeof

from pycoshark.mongomodels import Project, VCSSystem, File, Commit, FileAction, Issue, IssueSystem, Refactoring, Hunk
from pycoshark.utils import create_mongodb_uri_string, git_tag_filter, get_affected_versions, java_filename_filter, jira_is_resolved_and_fixed

from util.git import CollectGit


class InducingMiner:
    """Mine inducing commits with the help of CollectGit and blame."""

    def __init__(self, logger, database, user, password, host, port, authentication, ssl, project_name, vcs_url, repo_path, repo_from_db=False):
        self._log = logger
        self._repo_path = repo_path
        self._project_name = project_name

        uri = create_mongodb_uri_string(user, password, host, port, authentication, ssl)
        connect(database, host=uri)

        pr = Project.objects.get(name=project_name)

        if vcs_url:
            vcs = VCSSystem.objects.get(project_id=pr.id, url=vcs_url)
        else:
            vcs = VCSSystem.objects.get(project_id=pr.id)

        its = IssueSystem.objects.get(project_id=pr.id)

        if 'jira' not in its.url:
            raise Exception('only jira issue systems are supported!')

        self._vcs_id = vcs.id
        self._its_id = its.id
        self._jira_key = its.url.split('project=')[-1]

        # we need to extract the repository from the MongoDB
        if repo_from_db:
            self.extract_repository(vcs, repo_path, project_name)

    def extract_repository(self, vcs, target_path, project_name):
        # fetch file
        repository = vcs.repository_file
        if not target_path.endswith('/'):
            target_path += '/'

        if repository.grid_id is None:
            raise Exception('no repository file for project!')

        fname = '{}.tar.gz'.format(project_name)

        # extract from gridfs
        with open(fname, 'wb') as f:
            f.write(repository.read())

        # extract tarfile
        with tarfile.open(fname, "r:gz") as tar_gz:
            def is_within_directory(directory, target):
                
                abs_directory = os.path.abspath(directory)
                abs_target = os.path.abspath(target)
            
                prefix = os.path.commonprefix([abs_directory, abs_target])
                
                return prefix == abs_directory
            
            def safe_extract(tar, path=".", members=None, *, numeric_owner=False):
            
                for member in tar.getmembers():
                    member_path = os.path.join(path, member.name)
                    if not is_within_directory(path, member_path):
                        raise Exception("Attempted Path Traversal in Tar File")
            
                tar.extractall(path, members, numeric_owner=numeric_owner) 
                
            
            safe_extract(tar_gz, target_path)

        # TODO: this will probably not work in every case
        repo_name = vcs.url.split('/')[-1].split('.')[0]
        self._repo_path = '{}{}/'.format(target_path, repo_name)
        self._log.info('using path %s', self._repo_path)

        # remove tarfile
        os.remove(fname)

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
        self._log.info('finished setting all FileAction.induces to []')

    def _find_boundary_date(self, issues, version_dates, affected_versions):
        """Find suspect boundary date.

        latest issue information but earliest date in commit (between created_at and affected versions)

        - latest creation date of linked bugs
        - earliest affected version
        """
        tags = git_tag_filter(self._project_name, discard_patch=False, correct_broken_tags=True)
        issue_dates = []
        affected_version_dates = []
        for issue in issues:

            if not issue.created_at:
                self._log.warn('no reporting date for issue {} id({}), ignoring it'.format(issue.external_id, issue.id))
                continue

            # direct link match, broken dates are already filtered in pycoshark so we do not need to do that here
            for av in issue.affects_versions:
                for tag in tags:
                    if av.lower() == tag['original'].lower():
                        rev = tag['revision']
                        if 'corrected_revision' in tag.keys():
                            rev = tag['corrected_revision']

                        c = Commit.objects(vcs_system_id=self._vcs_id, revision_hash=rev).only('committer_date').get()
                        affected_version_dates.append(c.committer_date)
                        self._log.debug('found direct link between tag: {} and affected version: {} using '.format(tag['original'], av))

            for av in get_affected_versions(issue, self._project_name, self._jira_key):
                avt = tuple(av)
                if avt in version_dates.keys():

                    for version_date in version_dates[avt]:
                        if version_date not in affected_version_dates:
                            affected_version_dates.append(version_date)
                else:
                    self._log.warn('affected version {} not found in git tags, skipping'.format(avt))

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

        self._log.debug('suspect boundary dates is {} from issue dates: {} and affected_versions: {}, use affected versions? {}'.format(suspect_boundary_date, issue_dates, affected_version_dates, affected_versions))
        return suspect_boundary_date

    def _collect_version_dates(self):
        """Match affected versions from the ITS to tag names from the VCS.

        3.0.0 from ITS matches 3.0.0 from VCS
        3.0 from ITS matches all of 3.0.X from VCS
        """
        tags = git_tag_filter(self._project_name, discard_patch=False, correct_broken_tags=True)

        # collect tags and their version and date used in this VCS system
        tag_versions = {}
        for t in tags:
            # the tag could point to a revision with a wrong date (e.g., via faulty subversion to git migrations)
            # in those cases git_tag_filter provides a corrected hash which we can use
            rev = t['revision']
            if 'corrected_revision' in t.keys():
                rev = t['corrected_revision']
            c = Commit.objects.get(vcs_system_id=self._vcs_id, revision_hash=rev)
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

    def refactoring_lines(self, commit_id, file_action_id):
        """Return lines from one file in one commit which are detected as Refactorings by rMiner.
        """
        lines = []
        for r in Refactoring.objects.filter(commit_id=commit_id, detection_tool='rMiner'):
            for h in r.hunks:
                # we skip added refactoring positions as they can not be blamed later
                if h['mode'].lower() == 'a':
                    continue

                # todo: only include before refactorings as we only blame (ofc) deleted lines
                h2 = Hunk.objects.get(id=h['hunk_id'])
                if h2.file_action_id == file_action_id:
                    lines.append((h['start_line'], h['end_line']))
        return lines

    def bug_fixing_lines(self, file_action_id):
        """Return lines which are validated as bug-fixing."""
        lines = []
        for h in Hunk.objects.filter(file_action_id=file_action_id):
            _, del_lines = self._transform_bugfix_lines(h)
            lines += del_lines
        return lines

    def _transform_bugfix_lines(self, hunk):
        """Transform validated hunk lines to file line numbers."""
        added_lines = []
        deleted_lines = []

        del_line = hunk.old_start
        add_line = hunk.new_start

        bugfix_lines_added = []
        bugfix_lines_deleted = []
        for hunk_line, line in enumerate(hunk.content.split('\n')):

            tmp = line[1:].strip()

            if line.startswith('+'):
                added_lines.append((add_line, tmp))
                if 'bugfix' in hunk.lines_verified.keys() and hunk_line in hunk.lines_verified['bugfix']:
                    bugfix_lines_added.append(add_line)
                del_line -= 1
            if line.startswith('-'):
                deleted_lines.append((del_line, tmp))
                if 'bugfix' in hunk.lines_verified.keys() and hunk_line in hunk.lines_verified['bugfix']:
                    bugfix_lines_deleted.append(del_line)
                add_line -= 1

            del_line += 1
            add_line += 1

        return bugfix_lines_added, bugfix_lines_deleted

    def write_bug_inducing(self, label='validated_bugfix', inducing_strategy='code_only', java_only=True, affected_versions=False, ignore_refactorings=True, name=None, only_validated_bugfix_lines=False):
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

        # depending on our label we restrict the selection to commits that contain linked issues in the respective list
        if label == 'validated_bugfix':
            params['fixed_issue_ids__0__exists'] = True
        elif label == 'adjustedszz_bugfix':
            params['szz_issue_ids__0__exists'] = True
        elif label == 'issueonly_bugfix':
            params['linked_issue_ids__0__exists'] = True
        elif label == 'issuefasttext_bugfix':
            params['linked_issue_ids__0__exists'] = True
        else:
            raise Exception('unknown label')

        all_changes = {}

        # fetch before instead of iterate over the cursor because of timeout
        bugfix_commit_ids = [c.id for c in Commit.objects.filter(**params).only('id').timeout(False)]  # maybe list comprehension will close the cursor
        for bugfix_commit_id in bugfix_commit_ids:

            bugfix_commit = Commit.objects.only('revision_hash', 'id', 'fixed_issue_ids', 'szz_issue_ids', 'linked_issue_ids', 'committer_date').get(id=bugfix_commit_id)

            # only modified files
            for fa in FileAction.objects.filter(commit_id=bugfix_commit.id, mode='M').timeout(False):
                f = File.objects.get(id=fa.file_id)

                # only java files
                if java_only and not java_filename_filter(f.path.lower()):
                    continue

                if label == 'validated_bugfix':
                    fixed_issue_ids = bugfix_commit.fixed_issue_ids
                elif label == 'adjustedszz_bugfix':
                    fixed_issue_ids = bugfix_commit.szz_issue_ids
                elif label == 'issueonly_bugfix':
                    fixed_issue_ids = bugfix_commit.linked_issue_ids
                elif label == 'issuefasttext_bugfix':
                    fixed_issue_ids = bugfix_commit.linked_issue_ids
                else:
                    raise Exception('unknown label')

                # only issues that are really closed and fixed:
                issues = []
                for issue_id in fixed_issue_ids:
                    try:
                        issue = Issue.objects.get(id=issue_id)
                    except Issue.DoesNotExist:
                        continue

                    # issueonly_bugfix considers linked_issue_ids, those may contain non-bugs
                    if label in ['issueonly_bugfix', 'adjustedszz_bugfix', 'issuefasttext_bugfix'] and str(issue.issue_type).lower() != 'bug':
                        continue

                    if not jira_is_resolved_and_fixed(issue):
                        continue

                    if label == 'validated_bugfix':
                        if not issue.issue_type_verified or issue.issue_type_verified.lower() != 'bug':
                            continue

                    issues.append(issue)

                if not issues:
                    self._log.warn('skipping commit {} as none of its issue_ids {} are closed/fixed/resolved'.format(bugfix_commit.revision_hash, fixed_issue_ids))
                    continue

                suspect_boundary_date = self._find_boundary_date(issues, self._version_dates, affected_versions)

                # if ignore refactorings
                ignore_lines = False
                if ignore_refactorings:
                    # get lines where refactorings happened
                    # pass them to the blame call
                    ignore_lines = self.refactoring_lines(bugfix_commit.id, fa.id)

                validated_bugfix_lines = False
                if only_validated_bugfix_lines:
                    validated_bugfix_lines = self.bug_fixing_lines(fa.id)

                # find bug inducing commits, add to our list for this commit and file
                for blame_commit, original_file in self._cg.blame(bugfix_commit.revision_hash, f.path, inducing_strategy, ignore_lines, validated_bugfix_lines):
                    blame_c = Commit.objects.only('id', 'committer_date', 'labels').get(vcs_system_id=self._vcs_id, revision_hash=blame_commit)

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
                    for blame_fa in FileAction.objects.filter(commit_id=blame_c.id).timeout(False):
                        blame_f = File.objects.get(id=blame_fa.file_id)

                        if blame_f.path == original_file:
                            key = str(fa.id) + '_' + str(blame_fa.id)

                            if key not in all_changes.keys():
                                all_changes[key] = {'change_file_action_id': fa.id, 'inducing_file_action': blame_fa.id, 'label': label, 'szz_type': szz_type, 'inducing_strategy': inducing_strategy}

        self._log.info('size of all changes: %s mb', asizeof.asizeof(all_changes) / 1024 / 1024)

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
