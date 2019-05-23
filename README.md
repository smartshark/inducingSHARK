# inducingSHARK

Find bug-inducing commits for smartSHARK.
InducingSHARK first collects bug-fix commits by the passed label, e.g., issueonly_bugfix, then it iterates over the fixing hunks for the commit and blames all changed lines.

It saves the bug-inducing 

## Install

### via PIP
```bash
pip install https://github.com/smartshark/inducingSHARK/zipball/master
```

### via setup.py
```bash
python setup.py install
```

## Run Tests
```bash
python setup.py test
```

## Execution for smartSHARK

InducingSHARK needs an already checked out repository. It also depends on a running MongoDB and that the MongoDB is filled for this project by vcsSHARK, labelSHARK and linkSHARK.
```bash
# inducingSHARK is executed on an already checked out revision $REVISION in a folder $PATH_TO_REPOSITORY
python inducingSHARK/smartshark_plugin.py -pn $PROJECT_NAME -U $DBUSER -P $DBPASS -DB $DBNAME -i $PATH_TO_REPOSITORY -u $REPOSITORY_GIT_URI -a $AUTHENTICATION_DB -l $COMMITLABEL -is $INDUCING_STRATEGY
```