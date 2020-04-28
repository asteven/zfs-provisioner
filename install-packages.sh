#!/bin/sh

set -eu

_apk() {
   apk -X "@edge http://dl-cdn.alpinelinux.org/alpine/edge/main" \
      $*
}

# Update the package listing, so we know what packages exist:
_apk update

# Install security updates:
_apk upgrade

# Install our dependencies:
_apk add zfs@edge

# Delete cached files we don't need anymore:
rm /var/cache/apk/*

