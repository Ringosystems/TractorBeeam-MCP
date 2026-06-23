"""Verified VB365 v8 REST path templates and per-workload restore maps.

Every template here was confirmed against the live OpenAPI spec
(GET https://<host>:4443/swagger/v8/swagger.json — 419 paths) rather than
guessed, so the action/restore tiers post to real endpoints. Paths are relative
to the API-version base (e.g. ".../v8/").
"""
from __future__ import annotations

# --- job / copy-job control (POST, no or trivial body) ----------------------
JOB_START = "Jobs/{job_id}/start"          # body: {"full": bool}
JOB_STOP = "Jobs/{job_id}/stop"
JOB_ENABLE = "Jobs/{job_id}/enable"
JOB_DISABLE = "Jobs/{job_id}/disable"
JOB_EXPLORE = "Jobs/{job_id}/explore"      # body: RESTExploreOptions -> opens a RestoreSession

# --- organization ops -------------------------------------------------------
ORG_SYNC = "Organizations/{org_id}/Sync"   # body: {"type": "Incremental"|"Full"}
ORG_SYNC_STATE = "Organizations/{org_id}/SyncState"
ORG_EXPLORE = "Organizations/{org_id}/explore"  # body: RESTOrganizationExploreOptions
ORG_USERS = "Organizations/{org_id}/Users"
ORG_GROUPS = "Organizations/{org_id}/Groups"
ORG_SITES = "Organizations/{org_id}/Sites"
ORG_TEAMS = "Organizations/{org_id}/Teams"
ORG_USER_ONEDRIVES = "Organizations/{org_id}/Users/{user_id}/OneDrives"

# --- proxy maintenance ------------------------------------------------------
PROXY_RESCAN = "Proxies/{proxy_id}/Rescan"
PROXY_MAINT_ENABLE = "Proxies/{proxy_id}/maintenance/enable"
PROXY_MAINT_DISABLE = "Proxies/{proxy_id}/maintenance/disable"

# --- reports (POST -> binary file) ------------------------------------------
# body: {"organizationId": str?, "format": "PDF"|"CSV", "timezone": str?}
REPORTS = {
    "mailbox_protection": "Reports/GenerateMailboxProtection",
    "onedrive_protection": "Reports/GenerateOneDriveProtection",
    "sharepoint_protection": "Reports/GenerateSharepointProtection",
    "teams_protection": "Reports/GenerateTeamsProtection",
    "user_protection": "Reports/GenerateUserProtection",
    "license_overview": "Reports/GenerateLicenseOverview",
    "storage_consumption": "Reports/GenerateStorageConsumption",
}

# --- restore sessions -------------------------------------------------------
RESTORE_SESSION = "RestoreSessions/{rs_id}"
RESTORE_SESSION_STOP = "RestoreSessions/{rs_id}/Stop"
RESTORE_SESSION_STATS = "RestoreSessions/{rs_id}/Statistics"
RESTORE_SESSION_EVENTS = "RestoreSessions/{rs_id}/Events"

# Explore type codes that select the workload of a restore session.
WORKLOAD_TO_EXPLORE_TYPE = {
    "exchange": "Vex",
    "onedrive": "Veod",
    "sharepoint": "Vesp",
    "teams": "Vet",
}

# Per-workload browse + restore map, relative to RestoreSessions/{rs_id}/ .
# Each entry: collection root, item path under a parent, and the supported
# restore verbs (original/alternate/export) at item granularity.
WORKLOADS = {
    "exchange": {
        "type": "Vex",
        "root": "organization/mailboxes",                      # list mailboxes
        "search": "organization/searchExchange",               # body: {"query": str}
        "items": "organization/mailboxes/{parent_id}/items",   # list items in a mailbox
        "folders": "organization/mailboxes/{parent_id}/folders",
        "restore_original": "organization/mailboxes/{parent_id}/items/restore",
        "restore_alternate": "organization/mailboxes/{parent_id}/items/restoreTo",
        "export": "organization/mailboxes/{parent_id}/items/exportToPst",
        "item_id_field": "RESTExchangeItemStringId",           # body item shape: {"id": ...}
    },
    "onedrive": {
        "type": "Veod",
        "root": "Organization/OneDrives",
        "search": "Organization/OneDrives/{parent_id}/search",
        "items": "Organization/OneDrives/{parent_id}/Documents",
        "folders": "Organization/OneDrives/{parent_id}/Folders",
        "restore_original": "Organization/OneDrives/{parent_id}/Documents/restore",
        "restore_alternate": "Organization/OneDrives/{parent_id}/Documents/copyTo",
        "export": "Organization/OneDrives/{parent_id}/Documents/save",
        "item_id_field": "id",
    },
    "sharepoint": {
        "type": "Vesp",
        "root": "Organization/Sites",
        "search": "Organization/Sites/{parent_id}/search",
        "items": "Organization/Sites/{parent_id}/Documents",
        "folders": "Organization/Sites/{parent_id}/Folders",
        "restore_original": "Organization/Sites/{parent_id}/Documents/restore",
        "restore_alternate": "Organization/Sites/{parent_id}/Documents/restoreTo",
        "export": "Organization/Sites/{parent_id}/Documents/save",
        "item_id_field": "id",
    },
    "teams": {
        "type": "Vet",
        "root": "organization/teams",
        "search": "organization/searchTeams",
        "items": "organization/teams/{parent_id}/channels",
        "folders": "organization/teams/{parent_id}/files",
        "restore_original": "organization/teams/{parent_id}/restore",
        "restore_alternate": "organization/teams/{parent_id}/restore",
        "export": "organization/teams/{parent_id}/posts/export",
        "item_id_field": "id",
    },
}
