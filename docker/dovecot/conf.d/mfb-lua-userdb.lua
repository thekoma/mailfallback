-- mfb-lua-userdb.lua -- Dovecot 2.4 Lua userdb for MailFallBack
--
-- Fetches dynamic namespaces from the MFB internal API at login time.
-- Each user's accounts are returned as separate Dovecot namespaces,
-- enabling UUID-based maildir paths per account.
--
-- The FIRST account overrides the default inbox namespace's global
-- mail_path/mail_driver/mailbox_list_layout fields directly.
-- Additional accounts create new namespaces via "namespace +=".

local json = require "json"

local http_client = dovecot.http.client {
    connect_timeout = "5s",
    request_timeout = "10s",
    request_max_attempts = 3,
}

local API_BASE = os.getenv("MFB_USERDB_URL") or "http://mailfallback:8000"
local API_KEY = os.getenv("DOVECOT_API_KEY") or ""


function script_init()
    dovecot.i_info("mfb-lua-userdb: initialized, API base = " .. API_BASE)
    return 0
end

function script_deinit()
end


function auth_userdb_lookup(req)
    local user = req.user
    dovecot.i_info("mfb-lua-userdb: lookup for user=" .. user)

    local url = API_BASE .. "/api/internal/dovecot/userdb/" .. user
    local http_req = http_client:request {
        url = url,
        method = "GET",
    }
    http_req:add_header("X-API-Key", API_KEY)

    local resp = http_req:submit()
    local status = resp:status()

    if status == 404 then
        dovecot.i_info("mfb-lua-userdb: user not found: " .. user)
        return dovecot.auth.USERDB_RESULT_USER_UNKNOWN, "user not found"
    end

    if status ~= 200 then
        dovecot.i_error("mfb-lua-userdb: API returned status " .. tostring(status)
            .. " for user " .. user)
        return dovecot.auth.USERDB_RESULT_INTERNAL_FAILURE, "API error"
    end

    local body = resp:payload()
    local ok, data = pcall(json.decode, body)
    if not ok then
        dovecot.i_error("mfb-lua-userdb: JSON parse error: " .. tostring(data))
        return dovecot.auth.USERDB_RESULT_INTERNAL_FAILURE, "JSON parse error"
    end

    local fields = {
        uid = tostring(data.uid),
        gid = tostring(data.gid),
        home = data.home,
    }

    -- Empty inbox namespace at the root — accounts go under prefixed namespaces
    local ns_names = {"mfb_root"}
    fields["namespace/mfb_root/inbox"] = "yes"
    fields["namespace/mfb_root/prefix"] = ""
    fields["namespace/mfb_root/separator"] = "/"
    fields["namespace/mfb_root/mail_driver"] = "maildir"
    fields["namespace/mfb_root/mail_path"] = data.home .. "/root-inbox"

    if data.namespaces then
        for _, ns in ipairs(data.namespaces) do
            local name = ns.name
            table.insert(ns_names, name)

            fields["namespace/" .. name .. "/mail_driver"] = ns.mail_driver or "maildir"
            fields["namespace/" .. name .. "/mail_path"] = ns.mail_path
            fields["namespace/" .. name .. "/mail_inbox_path"] = ns.mail_path .. "/INBOX"
            fields["namespace/" .. name .. "/separator"] = "/"
            fields["namespace/" .. name .. "/prefix"] = ns.prefix or ""
            fields["namespace/" .. name .. "/inbox"] = "no"
        end
    end

    fields["namespace"] = table.concat(ns_names, " ")

    local ns_count = data.namespaces and #data.namespaces or 0
    dovecot.i_info("mfb-lua-userdb: returning " .. tostring(ns_count)
        .. " account(s) for user " .. user)
    for k, v in pairs(fields) do
        dovecot.i_debug("mfb-lua-userdb:   " .. k .. " = " .. v)
    end

    return dovecot.auth.USERDB_RESULT_OK, fields
end


function auth_userdb_iterate()
    return {}
end
