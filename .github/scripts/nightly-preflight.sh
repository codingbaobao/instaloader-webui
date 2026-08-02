#!/usr/bin/env bash
set -Eeuo pipefail

: "${EVENT_NAME:?EVENT_NAME must be set}"
: "${SOURCE_REF:?SOURCE_REF must be set}"
: "${SOURCE_SHA:?SOURCE_SHA must be set}"
: "${REPOSITORY:?REPOSITORY must be set}"
: "${GITHUB_OUTPUT:?GITHUB_OUTPUT must be set}"
: "${GH_TOKEN:?GH_TOKEN must be set}"

if [[ "${SOURCE_REF}" != "refs/heads/main" ]]; then
  echo "::error::Nightly images may only be published from main." >&2
  exit 1
fi

printf 'source_sha=%s\n' "${SOURCE_SHA}" >> "${GITHUB_OUTPUT}"

if [[ "${EVENT_NAME}" == "schedule" ]]; then
  previous_sha="$(
    gh api \
      -H "Accept: application/vnd.github+json" \
      -H "X-GitHub-Api-Version: 2022-11-28" \
      "/repos/${REPOSITORY}/actions/workflows/nightly.yml/runs?branch=main&status=success&per_page=1" \
      --jq '.workflow_runs[0].head_sha // ""'
  )"
  if [[ "${previous_sha}" == "${SOURCE_SHA}" ]]; then
    echo "::notice::No new commits since the last successful nightly build."
    printf 'publish=false\n' >> "${GITHUB_OUTPUT}"
    exit 0
  fi
fi

ci_sha="$(
  gh api \
    -H "Accept: application/vnd.github+json" \
    -H "X-GitHub-Api-Version: 2022-11-28" \
    "/repos/${REPOSITORY}/actions/workflows/ci.yml/runs?branch=main&event=push&status=success&per_page=1" \
    --jq '.workflow_runs[0].head_sha // ""'
)"
if [[ "${ci_sha}" != "${SOURCE_SHA}" ]]; then
  echo "::error::Source commit ${SOURCE_SHA} does not have a successful CI run on main." >&2
  exit 1
fi

printf 'publish=true\n' >> "${GITHUB_OUTPUT}"
