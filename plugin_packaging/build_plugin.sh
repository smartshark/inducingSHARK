#!/bin/bash

current=`pwd`
mkdir -p /tmp/inducingSHARK/
cp * /tmp/inducingSHARK/
cp ../setup.py /tmp/inducingSHARK/
cp -R ../inducingSHARK/* /tmp/inducingSHARK/
cd /tmp/inducingSHARK/

tar -cvf "$current/inducingSHARK_plugin.tar" --exclude=*.tar --exclude=build_plugin.sh --exclude=*/tests --exclude=*/__pycache__ --exclude=*.pyc *
