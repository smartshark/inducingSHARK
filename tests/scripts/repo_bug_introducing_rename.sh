#!/bin/bash

cd $1

git init
git config user.name "Test User"
git config user.email "test@test.local"


export GIT_COMMITTER_DATE="2018-01-01 03:01:01 +0200"
export GIT_AUTHOR_DATE="2018-01-01 03:01:01 +0200"

cat << EOF > ./test2.py
def c():
    pass
EOF

git add test2.py
git commit -m "(a) init, added test2.py"


export GIT_COMMITTER_DATE="2018-01-03 03:01:01 +0200"
export GIT_AUTHOR_DATE="2018-01-03 03:01:01 +0200"

cat << EOF > ./test2.py
def c():
    print('hallo Welt')
EOF

git add test2.py
git commit -m "(b) add output"


export GIT_COMMITTER_DATE="2018-01-04 03:01:01 +0200"
export GIT_AUTHOR_DATE="2018-01-04 03:01:01 +0200"

mv test2.py test1.py
git add test1.py
git commit -a -m "(c) move file"


export GIT_COMMITTER_DATE="2018-01-05 03:01:01 +0200"
export GIT_AUTHOR_DATE="2018-01-05 03:01:01 +0200"

cat << EOF > ./test1.py
def c():
    pass
EOF

git add test1.py
git commit -m "(d) fix, remove unneeded output"
