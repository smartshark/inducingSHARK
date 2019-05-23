#!/usr/bin/env python

import logging

from mongoengine import connect
from pycoshark.mongomodels import Project, VCSSystem, File, Commit, CodeEntityState
from pycoshark.utils import reate_mongodb_uri_string


class MongoDb(object):
    """This class just wraps the Mongo connection code and the query for fetching the correct CodeEntityState for inserting the AST information."""

    def __init__(self, database, user, password, host, port, authentication, ssl, project_name, vcs_url, revision):
        self.project_name = project_name
        self.vcs_url = vcs_url
        self.revision = revision
        self.database = database
        self.uri = create_mongodb_uri_string(user, password, host, port, authentication, ssl)
        self._log = logging.getLogger('coastSHARK')

    def connect(self):
        connect(self.database, host=self.uri)

    def write_inducing_commits(self, filepath, imports):
        """Write inducing commits.

        :param str filepath: The full path of this file.
        :param list imports: A list of strings containing the imports.
        """
        project = Project.objects.get(name=self.project_name)
        vcs = VCSSystem.objects.get(url=self.vcs_url, project_id=project.id)
        c = Commit.objects.get(revision_hash=self.revision, vcs_system_id=vcs.id)
        f = File.objects.get(path=filepath, vcs_system_id=vcs.id)

        s_key = get_code_entity_state_identifier(filepath, c.id, f.id)

        CodeEntityState.objects(s_key=s_key).upsert_one(imports=imports, ce_type='file', long_name=filepath, commit_id=c.id, file_id=f.id)
