#!/bin/bash

cd $1

git init
git config user.name "Test User"
git config user.email "test@test.local"

export GIT_COMMITTER_DATE="2018-01-01 03:01:01 +0200"
export GIT_AUTHOR_DATE="2018-01-01 03:01:01 +0200"

cat << EOF > ./test2.py
def c():
    //passABCDCEFZ
    pass
EOF

git add test2.py
git commit -m "(a) init, added test2.py"


export GIT_COMMITTER_DATE="2018-01-03 03:01:01 +0200"
export GIT_AUTHOR_DATE="2018-01-03 03:01:01 +0200"

cat << EOF > ./test2.py
def c():
    //passABCDCEFZ
    print('hallo Welt')
EOF

git add test2.py
git commit -m "(b) introducing, add output"



export GIT_COMMITTER_DATE="2018-01-04 03:01:01 +0200"
export GIT_AUTHOR_DATE="2018-01-04 03:01:01 +0200"

cat << EOF > ./test2.py
def c():
    passABCDCEF
    pass
EOF

git add test2.py
git commit -m "(c) fix, remove unneeded output"
