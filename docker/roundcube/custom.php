<?php
$config['db_prefix'] = 'rc_';
$config['use_subscriptions'] = false;
$config['check_all_folders'] = true;
$config['disabled_actions'] = ['mail.compose'];
$config['search_mods'] = [
    '*' => ['subject' => 1, 'from' => 1, 'body' => 1],
];
$config['search_scope'] = 'base';

$oauth_client_id = getenv('ROUNDCUBE_OAUTH_CLIENT_ID');
if ($oauth_client_id) {
    $config['oauth_provider'] = 'generic';
    $config['oauth_provider_name'] = 'Authentik';
    $config['oauth_client_id'] = $oauth_client_id;
    $config['oauth_client_secret'] = getenv('ROUNDCUBE_OAUTH_CLIENT_SECRET');
    $config['oauth_auth_uri'] = getenv('ROUNDCUBE_OAUTH_AUTH_URI');
    $config['oauth_token_uri'] = getenv('ROUNDCUBE_OAUTH_TOKEN_URI');
    $config['oauth_identity_uri'] = getenv('ROUNDCUBE_OAUTH_IDENTITY_URI');
    $config['oauth_scope'] = 'openid email profile offline_access';
    $config['oauth_identity_fields'] = ['preferred_username'];
    $config['oauth_login_redirect'] = false;
}
