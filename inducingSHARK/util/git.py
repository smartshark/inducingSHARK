#!/usr/bin/env python
# -*- coding: utf-8 -*-

"""
This module provides a wrapper around the basic pygit2 functionality for collecting repository information.
"""

import os
import re
import logging
import subprocess
from datetime import datetime, timezone

import networkx as nx
from pygit2 import Repository, GIT_DIFF_FIND_RENAMES, GIT_DIFF_FIND_COPIES, GIT_DIFF_FIND_RENAMES_FROM_REWRITES, GIT_OBJ_TAG, GIT_BLAME_TRACK_COPIES_SAME_FILE


class CollectGit(object):
    """
    Small Helper class for small repositories.
    This does not scale because we hold a lot of data in memory.
    """

    _regex_comment = re.compile(r"(//[^\"\n\r]*(?:\"[^\"\n\r]*\"[^\"\n\r]*)*[\r\n]|/\*([^*]|\*(?!/))*?\*/)(?=[^\"]*(?:\"[^\"]*\"[^\"]*)*$)")
    _regex_jdoc_line = re.compile(r"(- |\+)\s*(\*|/\*).*")

    def __init__(self, path):
        if not path.endswith('.git'):
            if not path.endswith('/'):
                path += '/'
            path += '.git'
        self._log = logging.getLogger(self.__class__.__name__)
        self._path = path
        self._repo = Repository(self._path)
        self._hunks = {}

        self._file_actions = {}
        self._bugfix = {}
        self._msgs = {}
        self._days = {}
        self._cdays = {}
        self._branches = {}
        self._tags = {}

        self._dopts = GIT_DIFF_FIND_RENAMES | GIT_DIFF_FIND_COPIES
        self._SIMILARITY_THRESHOLD = 50
        self._graph = nx.DiGraph()

    @classmethod
    def clone_repo(cls, uri, local_path):
        project_name = uri.split('/')[-1].split('.git')[0]
        repo_path = local_path + '/' + project_name + '/'

        if os.path.isdir(repo_path):
            c = subprocess.run(['git', 'fetch'], cwd=repo_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if c.returncode != 0:
                err = 'Error pulling repository {} to {}'.format(uri, repo_path)
                raise Exception(err)
        else:
            os.mkdir(repo_path)
            c = subprocess.run(['git', 'clone', uri, repo_path], cwd=repo_path, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
            if c.returncode != 0:
                err = 'Error cloning repository {} to {}'.format(uri, repo_path)
                raise Exception(err)
        return repo_path

    def _changed_lines(self, hunk):
        added_lines = []
        deleted_lines = []

        del_line = hunk['old_start']
        add_line = hunk['new_start']

        for line in hunk['content'].split('\n'):

            tmp = line[1:].strip()
            # is_comment = tmp.startswith('//') or tmp.startswith('/*') or tmp.startswith('*')

            if line.startswith('+'):
                added_lines.append((add_line, tmp))
                del_line -= 1
            if line.startswith('-'):
                deleted_lines.append((del_line, tmp))
                add_line -= 1

            del_line += 1
            add_line += 1

        return added_lines, deleted_lines

    def _comment_only_change(self, content):
        content = content + '\n'  # required for regex to drop comments
        content = re.sub(self._regex_comment, "", content)
        removed = ''
        added = ''
        for line in content.split('\n'):
            line = re.sub(r"\s+", " ", line, flags=re.UNICODE)  # replace all kinds of whitespaces (also multiple) with sińgle whitespace
            if not re.match(self._regex_jdoc_line, line):
                if line.startswith('-'):
                    removed += line[1:].strip()
                elif line.startswith('+'):
                    added += line[1:].strip()
        return removed == added

    def _blame_lines(self, revision_hash, filepath, strategy, ignore_lines=False, validated_bugfix_lines=False):
        """We want to find changed lines for one file in one commit (from the previous commit).

        For this we are iterating over the diff and counting the lines that are deleted (changed) from the original file.
        We ignore all added lines.

        ignore_lines is already specific to all changed hunks of the file for which blame_lines is called
        """
        changed_lines = []
        if revision_hash not in self._hunks.keys():
            return changed_lines

        for h in self._hunks[revision_hash]:
            if h['new_file'] != filepath:
                continue

            # only whitespace or comment changes in the hunk, ignore
            if strategy == 'code_only' and self._comment_only_change(h['content']):
                self._log.debug('detected whitepace or comment only change in {} for {}'.format(revision_hash, filepath))
                continue

            added, deleted = self._changed_lines(h)
            for dt in deleted:
                if dt not in changed_lines and dt[1]:
                    if strategy == 'code_only' and dt[1].startswith(('//', '/*', '*')):
                        continue

                    # we may only want validated lines
                    if validated_bugfix_lines is not False:
                        if dt[0] not in validated_bugfix_lines:
                            continue

                    # we may ignore lines, e.g., refactorings
                    if ignore_lines:
                        ignore = False
                        for start_line, end_line in ignore_lines:
                            if start_line <= dt[0] <= end_line:
                                ignore = True
                                break

                        # if we hit the line in our ignore list we continue to the next
                        if ignore:
                            # self._log.warn('ignore line {} in file {} in commit {} because of refactoring detection'.format(dt[0], filepath, revision_hash))
                            continue

                    changed_lines.append(dt)

        return changed_lines

    def blame(self, revision_hash, filepath, strategy='code_only', ignore_lines=False, validated_bugfix_lines=False):
        """Collect a list of commits where the given revision and file were last changed.

        Uses git blame.

        :param str revision_hash: Commit for which we want to collect blame commits.
        :param str filepath: File for which we want to collect blame commits.
        :rtype: list
        :returns: A list of tuples of blame commits and the original file for the given parameters.
        """
        commits = []

        # - ignore if commit is not in graph
        if revision_hash not in self._graph:
            return []

        # # - ignore package-info.java
        # if strategy == 'code_only' and filepath.lower().endswith('package-info.java'):
        #     self._log.debug('skipping blame on revision: {} for file {} because it is package-info.java'.format(revision_hash, filepath))
        #     return []

        # # - ignore test/ /test/ example/ examples/
        # if strategy == 'code_only' and re.match(self._regex_test_example, filepath):
        #     self._log.debug('skipping blame on revision: {} for file {} because it is a test or an example'.format(revision_hash, filepath))
        #     return []

        # bail on multiple parents
        parents = list(self._graph.predecessors(revision_hash))
        if len(parents) > 1:
            self._log.debug('skipping blame on revision: {} because it is a merge commit'.format(revision_hash))
            return []

        changed_lines = self._blame_lines(revision_hash, filepath, strategy, ignore_lines, validated_bugfix_lines)
        parent_commit = self._repo.revparse_single('{}^'.format(revision_hash))

        blame = self._repo.blame(filepath, flags=GIT_BLAME_TRACK_COPIES_SAME_FILE, newest_commit=parent_commit.hex)
        for lineno, line in changed_lines:
            # returns blamehunk for specific line
            try:
                bh = blame.for_line(lineno)
            except IndexError as e:
                # this happens when we have the wrong parent node
                bla = 'tried to get file: {}, line: {}, revision: {}, blame commit: {}'.format(filepath, lineno, revision_hash, str(bh.orig_commit_id))
                self._log.error(bla)
                raise  # this is critical

            inducing_commit = self._repo.revparse_single(str(bh.orig_commit_id))

            # start = bh.orig_start_line_number
            # lines = bh.lines_in_hunk
            # final_start = bh.final_start_line_number
            # print(revision_hash, '->', inducing_commit.hex)
            # print('original: {}: {}'.format(lineno, line))
            # print('{},{}: {},{}'.format(start, lines, final_start, lines))

            # blame_lines = []
            # for hunk in self._hunks[inducing_commit.hex]:
            #     if hunk['new_file'] != bh.orig_path:
            #         continue
            #     ls = final_start
            #     for i, blame_line in enumerate(hunk['content'].split('\n')):
            #         if blame_line[1:].strip() and line[1:].strip() and blame_line[1:] == line[1:]:
            #             print('blame: {}:{}'.format(ls, blame_line))
            #         ls += 1
            commits.append((inducing_commit.hex, bh.orig_path))

        # make unique
        return list(set(commits))

    def commit_information(self, revision_hash):
        obj = self._repo.get(revision_hash)

        return {'author_name': obj.author.name,
                'author_email': obj.author.email,
                'committer_name': obj.committer.name,
                'committer_email': obj.committer.email,
                'committer_date_utc': datetime.fromtimestamp(obj.commit_time, tz=timezone.utc),
                'committer_date': obj.commit_time,
                'committer_date_offset': obj.commit_time_offset,
                'message': obj.message,
                'file_actions': self._file_actions[revision_hash]}

    def file_actions(self, revision_hash):
        return self._file_actions[revision_hash]

    def all_files(self, revision_hash):
        # 1. checkout repo
        self._checkout_revision(revision_hash)

        # 2. list files
        return self._list_files()

    def first_occurence(self, filename):
        # file rename tracking is not possible currently in libgit, see:
        # https://github.com/libgit2/libgit2/issues/3041

        # find first occurence of file with git cli

        # git log --follow --diff-filter=A --find-renames=40% foo.js
        path = self._path.replace('.git', '')
        c = subprocess.run(['git', 'log', '--all', '--pretty=tformat:"%H %ci"', '--follow', '--diff-filter=A', '--find-renames=80%', '--', filename], cwd=path, stdout=subprocess.PIPE, stderr=subprocess.PIPE)
        if c.returncode != 0:
            err = 'Error finding first occurrence of file: {}'.format(filename)
            self._log.error(err)
            self._log.error(c.stderr)
            raise Exception(err)

        full = c.stdout.decode('utf-8')
        try:
            first_line = full.split('\n')[-2]
        except IndexError:
            if not full:
                print('no git log for file {}'.format(filename))
            print(full)
            raise
        first_date = ' '.join(first_line.split(' ')[1:]).replace('"', '')
        dt = datetime.strptime(first_date, '%Y-%m-%d %H:%M:%S %z')  # we can do this here because we control the input format, %z does not cover +01:00 just +100 (at least in 3.6)
        return dt

    def tags(self):
        regex = re.compile('^refs/tags')
        ret = []
        for tagref in filter(lambda r: regex.match(r), self._repo.listall_references()):
            tag = self._repo.lookup_reference(tagref)
            target = self._repo.lookup_reference(tagref).peel()
            ret.append({'name': tag.name.replace('refs/tags/', ''), 'revision_hash': target.id})
        return ret

    def _checkout_revision(self, revision):
        """Checkout via shell, we ignore stdout output."""
        path = self._path.replace('.git', '')
        c = subprocess.run(['git', 'checkout', '-q', '-f', revision], cwd=path, stdout=subprocess.PIPE)
        return c.returncode == 0

    def _list_files(self):
        """The slower list_files"""
        path = self._path.replace('.git', '')

        ret = []
        for root, dirs, files in os.walk(path):
            for file in files:
                filepath = os.path.join(root, file)
                relative_filepath = filepath.replace(path, '')
                ret.append(relative_filepath)
        return ret

    def _list_files2(self):
        """The faster list_files (relies on find command)"""
        path = self._path.replace('.git', '')
        lines = subprocess.check_output(['find', '.', '-iname', '*.java'], cwd=path)

        files = []
        for f in lines.decode('utf-8').split('\n'):
            if f.lower().endswith('.java'):
                files.append(f.replace('./', ''))

        return files

    def _changed_files(self, commit):
        changed_files = []
        diffs = []

        # for initial commit (or orphan commits) pygit2 needs some special attention
        initial = False
        if not commit.parents:
            initial = True
            diffs.append((None, commit.tree.diff_to_tree(context_lines=0, interhunk_lines=1)))

        # we may have multiple parents (merge commit)
        for parent in commit.parents:
            # we need all information from each parent because in a merge each parent may add different files
            tmp = self._repo.diff(parent, commit, context_lines=0, interhunk_lines=1)
            tmp.find_similar(self._dopts, self._SIMILARITY_THRESHOLD, self._SIMILARITY_THRESHOLD)
            diffs.append((parent.hex, tmp))

        for parent, diff in diffs:
            checked_paths = set()
            for patch in diff:
                if patch.delta.new_file.path in checked_paths:
                    self._log.warn('already have {} in checked_paths'.format(patch.delta.new_file.path))
                    continue
                mode = 'X'
                if patch.delta.status == 1:
                    mode = 'A'
                elif patch.delta.status == 2:
                    mode = 'D'
                elif patch.delta.status == 3:
                    mode = 'M'
                elif patch.delta.status == 4:
                    mode = 'R'
                elif patch.delta.status == 5:
                    mode = 'C'
                elif patch.delta.status == 6:
                    mode = 'I'
                elif patch.delta.status == 7:
                    mode = 'U'
                elif patch.delta.status == 8:
                    mode = 'T'

                # diff to tree gives D for inital commit otherwise
                if initial:
                    mode = 'A'

                # we may have hunks to add
                if patch.hunks and commit.hex not in self._hunks.keys():
                    self._hunks[commit.hex] = []

                # add hunks
                for hunk in patch.hunks:
                    # initial is special case
                    if initial:
                        content = ''.join(['+' + l.content for l in hunk.lines])
                        self._hunks[commit.hex].append({'header': hunk.header, 'new_file': patch.delta.new_file.path, 'new_start': hunk.old_start, 'new_lines': hunk.old_lines, 'old_start': hunk.new_start, 'old_lines': hunk.new_lines, 'content': content})
                    else:
                        content = ''.join([l.origin + l.content for l in hunk.lines])
                        self._hunks[commit.hex].append({'header': hunk.header, 'new_file': patch.delta.new_file.path, 'new_start': hunk.new_start, 'new_lines': hunk.new_lines, 'old_start': hunk.old_start, 'old_lines': hunk.old_lines, 'content': content})

                # collect line stats
                if initial:
                    fa = {'lines_added': patch.line_stats[2],
                          'lines_deleted': patch.line_stats[1],
                          'changeset_size': len(diff),
                          'parent': None}
                else:
                    fa = {'lines_added': patch.line_stats[1],
                          'lines_deleted': patch.line_stats[2],
                          'changeset_size': len(diff),
                          'parent': parent}

                #if mode == 'R':
                #    print('R {} -> {}, sim: {}'.format(patch.delta.old_file.path, patch.delta.new_file.path, patch.delta.similarity))

                if mode in ['C', 'R']:
                    changed_file = [mode, patch.delta.new_file.path, patch.delta.old_file.path, fa]
                else:
                    changed_file = [mode, patch.delta.new_file.path, None, fa]

                checked_paths.add(patch.delta.new_file.path)
                changed_files.append(changed_file)
        return changed_files

    def collect(self):
        # list all branches
        for branch in list(self._repo.branches):
            self._collect_branch(branch)

        # list all tags
        for obj in self._repo:
            tag = self._repo[obj]
            if tag.type == GIT_OBJ_TAG:
                self._collect_branch(tag, is_tag=True)

        return self._graph

    def _collect_branch(self, branch, is_tag=False):
        if type(branch) == str:
            branch = self._repo.branches[branch]

        # add nodes to graph
        try:
            for c in self._repo.walk(branch.target):
                self._graph.add_node(c.hex)

                # branch stuff, used for traversing backwards for tags in svn->git conversions
                if c.hex not in self._branches.keys():
                    self._branches[c.hex] = []

                # what about tags which are also on branches?
                if is_tag:
                    self._tags[c.hex] = branch.name
                else:
                    self._branches[c.hex].append(branch.branch_name)

                # add msg
                self._msgs[c.hex] = c.message

                # add days, we use this later for lookup
                day = str(datetime.fromtimestamp(c.commit_time, tz=timezone.utc).date())
                if day not in self._days.keys():
                    self._days[day] = []
                self._days[day].append(c.hex)

                # add for convenience for OntdekBaanBfs
                self._cdays[c.hex] = day

                # add changed files per node
                if c.hex not in self._file_actions.keys():
                    self._file_actions[c.hex] = self._changed_files(c)

            # add edges to graph
            for c in self._repo.walk(branch.target):
                for p in c.parents:
                    self._graph.add_edge(p.hex, c.hex)
        except ValueError as e:
            pass
            # self._log.error('skipping {}, error: {}'.format(branch, e))
            # self._log.exception(e)

        # add commit msgs
        # nx.set_node_attributes(self._graph, self._msgs, 'msg')
