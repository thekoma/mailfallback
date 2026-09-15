"""Values shared between layers that must not drift apart.

Deliberately dependency-free: config_generator imports this at boot, before
the database exists, and must not pull in models or services to do it.
"""

# The IMAP mailbox name of the restore staging area.
#
# It appears in three places that have to agree exactly, or the feature breaks
# in ways no single test would catch: the Maildir path
# (staging_service.staging_dir), the Dovecot ACL filter that makes it writable
# (config_generator._dovecot_acl_conf), and the webmail deep link in the
# restore workspace template.
#
# Not configurable, and not "Staging". Dovecot's ACL `mailbox` filters match
# the namespace-INTERNAL name with the namespace prefix stripped, so a filter
# on a plain name also matches a *provider* folder carrying that name — which
# handed out expunge and flag rights over real backed-up mail (#237). No filter
# syntax separates the two, so the defence is a name a provider folder
# realistically does not have.
STAGING_MAILBOX = "MFB-Staging"
