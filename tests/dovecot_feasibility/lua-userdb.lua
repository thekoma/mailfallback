-- lua-userdb.lua -- Dovecot 2.4 Lua userdb that fetches namespaces from MFB API
--
-- Uses:
--   dovecot.http.client{}  -- built-in HTTP client (lib-lua)
--   require("json")        -- luajson, bundled in dovecot/dovecot:latest-2.4
--
-- Returns namespace extra-fields so Dovecot creates dynamic namespaces
-- per account at login time.
--
-- Dovecot 2.4 namespace fields (NOT the old 2.3 "location" format):
--   namespace += <name>
--   namespace/<name>/mail_driver = maildir
--   namespace/<name>/mail_path = /data/mailboxes/<uuid>
--   namespace/<name>/mailbox_list_layout = fs
--   namespace/<name>/prefix = <prefix>
--   namespace/<name>/separator = /
--   namespace/<name>/inbox = yes|no

local json = require "json"

-- Persistent HTTP client -- reuses connections across lookups.
-- Dovecot 2.4.2 Lua HTTP client parameter names (discovered by trial):
--   connect_timeout (string with unit, e.g. "5s")
--   request_timeout (string with unit, e.g. "10s")
--   request_max_attempts (integer)
-- NOTE: The docs example uses "timeout"/"max_attempts"/"debug" but those
-- are rejected at runtime as "unknown setting" in v2.4.2.
local http_client = dovecot.http.client {
    connect_timeout = "5s",
    request_timeout = "10s",
    request_max_attempts = 3,
}

-- Base URL for the MFB internal API (mock-api in the test stack)
local API_BASE = "http://mock-api:8080"


function script_init()
    dovecot.i_info("lua-userdb: initialized, API base = " .. API_BASE)
    return 0
end

function script_deinit()
end


function auth_userdb_lookup(req)
    local user = req.user
    dovecot.i_info("lua-userdb: lookup for user=" .. user)

    -- Build request
    local url = API_BASE .. "/api/internal/dovecot/userdb/" .. user
    local http_req = http_client:request {
        url = url,
        method = "GET",
    }

    -- Submit and check response
    local resp = http_req:submit()
    local status = resp:status()

    if status == 404 then
        dovecot.i_info("lua-userdb: user not found: " .. user)
        return dovecot.auth.USERDB_RESULT_USER_UNKNOWN, "user not found"
    end

    if status ~= 200 then
        dovecot.i_error("lua-userdb: API returned status " .. tostring(status)
            .. " for user " .. user)
        return dovecot.auth.USERDB_RESULT_INTERNAL_FAILURE, "API error"
    end

    -- Parse JSON body
    local body = resp:payload()
    local ok, data = pcall(json.decode, body)
    if not ok then
        dovecot.i_error("lua-userdb: JSON parse error: " .. tostring(data))
        return dovecot.auth.USERDB_RESULT_INTERNAL_FAILURE, "JSON parse error"
    end

    -- Build the extra-fields table
    local fields = {
        uid = tostring(data.uid),
        gid = tostring(data.gid),
        home = data.home,
    }

    -- Collect namespace names for the "namespace +=" field
    local ns_names = {}

    -- Add namespace extra fields for each account.
    -- Dovecot 2.4 uses separate fields instead of the old "location" string:
    --   mail_driver, mail_path, mailbox_list_layout
    if data.namespaces then
        for _, ns in ipairs(data.namespaces) do
            local name = ns.name
            table.insert(ns_names, name)

            fields["namespace/" .. name .. "/mail_driver"] = ns.mail_driver or "maildir"
            fields["namespace/" .. name .. "/mail_path"] = ns.mail_path
            fields["namespace/" .. name .. "/mailbox_list_layout"] = ns.mailbox_list_layout or "fs"
            fields["namespace/" .. name .. "/prefix"] = ns.prefix or ""
            fields["namespace/" .. name .. "/separator"] = "/"

            if ns.inbox then
                fields["namespace/" .. name .. "/inbox"] = "yes"
            else
                fields["namespace/" .. name .. "/inbox"] = "no"
            end
        end
    end

    -- Tell Dovecot to create these namespaces (namespace += name1 name2 ...)
    -- Each name in the space-separated list creates a new namespace.
    if #ns_names > 0 then
        fields["namespace"] = table.concat(ns_names, " ")
    end

    -- Log what we are returning
    local ns_count = data.namespaces and #data.namespaces or 0
    dovecot.i_info("lua-userdb: returning " .. tostring(ns_count)
        .. " namespace(s) for user " .. user)
    for k, v in pairs(fields) do
        dovecot.i_debug("lua-userdb:   " .. k .. " = " .. v)
    end

    return dovecot.auth.USERDB_RESULT_OK, fields
end


function auth_userdb_iterate()
    -- Not needed for this test, return empty
    return {}
end
