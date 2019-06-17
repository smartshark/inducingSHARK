#!/bin/bash

cd $1

git init
git config user.name "Test User"
git config user.email "test@test.local"

export GIT_COMMITTER_DATE="2018-01-01 03:01:01 +0200"
export GIT_AUTHOR_DATE="2018-01-01 03:01:01 +0200"

cat << EOF > ./test1.py
dddd
aaaa
aaaa
aaaa
EOF

git add test1.py
git commit -m "(a) init"


export GIT_COMMITTER_DATE="2018-01-03 03:01:01 +0200"
export GIT_AUTHOR_DATE="2018-01-03 03:01:01 +0200"

cat << EOF > ./test1.py
dddd
bbbb
aaaa
bbbb
EOF

git add test1.py
git commit -m "(b) change two non consecutive lines"


export GIT_COMMITTER_DATE="2018-01-05 03:01:01 +0200"
export GIT_AUTHOR_DATE="2018-01-05 05:01:01 +0200"

cat << EOF > ./test1.py
cccc
cccc
extraline
aaaa
cccc
EOF

git add test1.py
git commit -m "(c) fix all b and d, add extraline"