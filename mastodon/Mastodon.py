# coding: utf-8

import os
import os.path
import mimetypes
import time
import random
import string
import datetime
from contextlib import closing
import pytz
import requests
from requests.models import urlencode
import dateutil
import dateutil.parser
import re
import copy
import threading
import sys
import six
from decorator import decorate

try:
    from urllib.parse import urlparse
except ImportError:
    from urlparse import urlparse

###
# Version check functions, including decorator and parser
###
def parse_version_string(version_string):
    """Parses a semver version string, stripping off "rc" stuff if present."""
    string_parts =  version_string.split(".")
    return [
        int(re.match("([0-9]*)", string_parts[0])[0]),
        int(re.match("([0-9]*)", string_parts[1])[0]),
        int(re.match("([0-9]*)", string_parts[2])[0]),
    ]

def api_version(created_ver, last_changed_ver):
    """Version check decorator. Currently only checks Bigger Than."""
    def api_min_version_decorator(function):  
        def wrapper(function, self, *args, **kwargs):
            if self.version_check_mode != "none":
                if self.version_check_mode == "created":
                    version = created_ver
                else:
                    version = last_changed_ver
                major, minor, patch = parse_version_string(version)
                if major > self.mastodon_major:
                    raise MastodonVersionError(f"Version check failed (Need version {version})")
                elif major == self.mastodon_major and minor > self.mastodon_minor:
                    raise MastodonVersionError(f"Version check failed (Need version {version})")
                elif major == self.mastodon_major and minor == self.mastodon_minor and patch > self.mastodon_patch:
                    raise MastodonVersionError(f"Version check failed (Need version {version})")
            return function(self, *args, **kwargs)

        function.__doc__ = function.__doc__ + "\n\n        *Added: Mastodon v" + created_ver + ", last changed: Mastodon v" + last_changed_ver + "*"
        return decorate(function, wrapper)

    return api_min_version_decorator

###
# Dict helper class.
# Defined at top level so it can be pickled.
###
class AttribAccessDict(dict):
    def __getattr__(self, attr):
        if attr in self:
            return self[attr]
        else:
            raise AttributeError(f"Attribute not found: {str(attr)}")
        
    def __setattr__(self, attr, val):
        if attr in self:
            raise AttributeError("Attribute-style access is read only")
        super().__setattr__(attr, val)

###
# The actual Mastodon class
###

class Mastodon:
    """
    Super basic but thorough and easy to use Mastodon
    api wrapper in python.

    If anything is unclear, check the official API docs at
    https://github.com/tootsuite/documentation/blob/master/Using-the-API/API.md
    """
    __DEFAULT_BASE_URL = 'https://mastodon.social'
    __DEFAULT_TIMEOUT = 300
    __SUPPORTED_MASTODON_VERSION = "2.2.0"
    
    ###
    # Registering apps
    ###
    @staticmethod
    def create_app(client_name, scopes=['read', 'write', 'follow'], redirect_uris=None, website=None, to_file=None,
                   api_base_url=__DEFAULT_BASE_URL, request_timeout=__DEFAULT_TIMEOUT):
        """
        Create a new app with given `client_name` and `scopes` (read, write, follow)

        Specify `redirect_uris` if you want users to be redirected to a certain page after authenticating.
        Specify `to_file` to persist your apps info to a file so you can use them in the constructor.
        Specify `api_base_url` if you want to register an app on an instance different from the flagship one.

        Presently, app registration is open by default, but this is not guaranteed to be the case for all
        future mastodon instances or even the flagship instance in the future.

        Returns `client_id` and `client_secret`, both as strings.
        """
        api_base_url = Mastodon.__protocolize(api_base_url)

        request_data = {
            'client_name': client_name,
            'scopes': " ".join(scopes)
        }

        try:
            if redirect_uris is not None:
                request_data['redirect_uris'] = redirect_uris
            else:
                request_data['redirect_uris'] = 'urn:ietf:wg:oauth:2.0:oob'
            if website is not None:
                request_data['website'] = website

            response = requests.post(
                f'{api_base_url}/api/v1/apps',
                data=request_data,
                timeout=request_timeout,
            )
            response = response.json()
        except Exception as e:
            raise MastodonNetworkError(f"Could not complete request: {e}")

        if to_file is not None:
            with open(to_file, 'w') as secret_file:
                secret_file.write(response['client_id'] + '\n')
                secret_file.write(response['client_secret'] + '\n')

        return (response['client_id'], response['client_secret'])

    ###
    # Authentication, including constructor
    ###
    def __init__(self, client_id, client_secret=None, access_token=None,
                 api_base_url=__DEFAULT_BASE_URL, debug_requests=False,
                 ratelimit_method="wait", ratelimit_pacefactor=1.1,
                 request_timeout=__DEFAULT_TIMEOUT, mastodon_version=None,
                 version_check_mode = "created"):
        """
        Create a new API wrapper instance based on the given `client_secret` and `client_id`. If you
        give a `client_id` and it is not a file, you must also give a secret.

        You can also specify an `access_token`, directly or as a file (as written by `log_in()`_).

        Mastodon.py can try to respect rate limits in several ways, controlled by `ratelimit_method`.
        "throw" makes functions throw a `MastodonRatelimitError` when the rate
        limit is hit. "wait" mode will, once the limit is hit, wait and retry the request as soon
        as the rate limit resets, until it succeeds. "pace" works like throw, but tries to wait in
        between calls so that the limit is generally not hit (How hard it tries to not hit the rate
        limit can be controlled by ratelimit_pacefactor). The default setting is "wait". Note that
        even in "wait" and "pace" mode, requests can still fail due to network or other problems! Also
        note that "pace" and "wait" are NOT thread safe.

        Specify `api_base_url` if you wish to talk to an instance other than the flagship one.
        If a file is given as `client_id`, client ID and secret are read from that file.

        By default, a timeout of 300 seconds is used for all requests. If you wish to change this,
        pass the desired timeout (in seconds) as `request_timeout`.
        
        The `mastodon_version` parameter can be used to specify the version of Mastodon that Mastodon.py will
        expect to be installed on the server. The function will throw an error if an unparseable 
        Version is specified. If no version is specified, Mastodon.py will set `mastodon_version` to the 
        detected version.
        
        The version check mode can be set to "created" (the default behaviour), "changed" or "none". If set to 
        "created", Mastodon.py will throw an error if the version of Mastodon it is connected to is too old
        to have an endpoint. If it is set to "changed", it will throw an error if the endpoints behaviour has
        changed after the version of Mastodon that is connected has been released. If it is set to "none",
        version checking is disabled.
        """
        self.api_base_url = Mastodon.__protocolize(api_base_url)
        self.client_id = client_id
        self.client_secret = client_secret
        self.access_token = access_token
        self.debug_requests = debug_requests
        self.ratelimit_method = ratelimit_method
        self._token_expired = datetime.datetime.now()
        self._refresh_token = None

        self.ratelimit_limit = 300
        self.ratelimit_reset = time.time()
        self.ratelimit_remaining = 300
        self.ratelimit_lastcall = time.time()
        self.ratelimit_pacefactor = ratelimit_pacefactor

        self.request_timeout = request_timeout

        # Versioning
        if mastodon_version is None:
            self.retrieve_mastodon_version()
        else:
            try:
                self.mastodon_major, self.mastodon_minor, self.mastodon_patch = parse_version_string(mastodon_version)
            except:
                raise MastodonVersionError("Bad version specified")

        if version_check_mode not in ["created", "changed", "none"]:
            raise MastodonIllegalArgumentError("Invalid version check method.")
        self.version_check_mode = version_check_mode

        # Ratelimiting parameter check
        if ratelimit_method not in ["throw", "wait", "pace"]:
            raise MastodonIllegalArgumentError("Invalid ratelimit method.")

        # Token loading
        if os.path.isfile(self.client_id):
            with open(self.client_id, 'r') as secret_file:
                self.client_id = secret_file.readline().rstrip()
                self.client_secret = secret_file.readline().rstrip()
        elif self.client_secret is None:
            raise MastodonIllegalArgumentError('Specified client id directly, but did not supply secret')

        if self.access_token is not None and os.path.isfile(self.access_token):
            with open(self.access_token, 'r') as token_file:
                self.access_token = token_file.readline().rstrip()
    
    def retrieve_mastodon_version(self):
        """
        Determine installed mastodon version and set major, minor and patch (not including RC info) accordingly.
        
        Returns the version string, possibly including rc info.
        """
        try:
            version_str = self.__instance()["version"]
        except:
            # instance() was added in 1.1.0, so our best guess is 1.0.0.
            version_str = "1.0.0"
            
        self.mastodon_major, self.mastodon_minor, self.mastodon_patch = parse_version_string(version_str)
        return version_str
        
    def verify_minimum_version(self, version_str):
        """
        Update version info from server and verify that at least the specified version is present.
        
        Returns True if version requirement is satisfied, False if not.
        """
        self.retrieve_mastodon_version()
        major, minor, patch = parse_version_string(version_str)
        if major > self.mastodon_major:
            return False
        elif major == self.mastodon_major and minor > self.mastodon_minor:
            return False
        elif major == self.mastodon_major and minor == self.mastodon_minor and patch > self.mastodon_patch:
            return False
        return True

    @staticmethod
    def get_supported_version():
        """
        Retrieve the maximum version of Mastodon supported by this version of Mastodon.py
        """
        return Mastodon.__SUPPORTED_MASTODON_VERSION
    
    def auth_request_url(self, client_id=None, redirect_uris="urn:ietf:wg:oauth:2.0:oob",
                         scopes=['read', 'write', 'follow']):
        """Returns the url that a client needs to request the grant from the server.
        """
        if client_id is None:
            client_id = self.client_id
        elif os.path.isfile(client_id):
            with open(client_id, 'r') as secret_file:
                client_id = secret_file.readline().rstrip()

        params = {
            'client_id': client_id,
            'response_type': "code",
            'redirect_uri': redirect_uris,
            'scope': " ".join(scopes),
        }
        formatted_params = urlencode(params)
        return "".join([self.api_base_url, "/oauth/authorize?", formatted_params])

    def log_in(self, username=None, password=None,
               code=None, redirect_uri="urn:ietf:wg:oauth:2.0:oob", refresh_token=None,
               scopes=['read', 'write', 'follow'], to_file=None):
        """
        Get the access token for a user.
        
        The username is the e-mail used to log in into mastodon.

        Can persist access token to file `to_file`, to be used in the constructor.

        Handles password and OAuth-based authorization.
        
        Will throw a `MastodonIllegalArgumentError` if username / password
        are wrong, scopes are not valid or granted scopes differ from requested.

        For OAuth2 documentation, compare
        https://github.com/doorkeeper-gem/doorkeeper/wiki/Interacting-as-an-OAuth-client-with-Doorkeeper

        Returns the access token as a string.
        """
        if username is not None and password is not None:
            params = self.__generate_params(locals(), ['scopes', 'to_file', 'code', 'refresh_token'])
            params['grant_type'] = 'password'
        elif code is not None:
            params = self.__generate_params(locals(), ['scopes', 'to_file', 'username', 'password', 'refresh_token'])
            params['grant_type'] = 'authorization_code'
        elif refresh_token is not None:
            params = self.__generate_params(locals(), ['scopes', 'to_file', 'username', 'password', 'code'])
            params['grant_type'] = 'refresh_token'
        else:
            raise MastodonIllegalArgumentError('Invalid arguments given. username and password or code are required.')

        params['client_id'] = self.client_id
        params['client_secret'] = self.client_secret
        params['scope'] = " ".join(scopes)

        try:
            response = self.__api_request('POST', '/oauth/token', params, do_ratelimiting=False)
            self.access_token = response['access_token']
            self.__set_refresh_token(response.get('refresh_token'))
            self.__set_token_expired(int(response.get('expires_in', 0)))
        except Exception as e:
            if username is not None or password is not None:
                raise MastodonIllegalArgumentError(
                    f'Invalid user name, password, or redirect_uris: {e}'
                )
            elif code is not None:
                raise MastodonIllegalArgumentError(
                    f'Invalid access token or redirect_uris: {e}'
                )
            else:
                raise MastodonIllegalArgumentError(f'Invalid request: {e}')

        requested_scopes = " ".join(sorted(scopes))
        received_scopes = " ".join(sorted(response["scope"].split(" ")))

        if requested_scopes != received_scopes:
            raise MastodonAPIError(
                f'Granted scopes "{received_scopes}" differ from requested scopes "{requested_scopes}".'
            )

        if to_file is not None:
            with open(to_file, 'w') as token_file:
                token_file.write(response['access_token'] + '\n')

        return response['access_token']

    ###
    # Reading data: Instances
    ###
    @api_version("1.1.0", "1.4.2")
    def instance(self):
        """
        Retrieve basic information about the instance, including the URI and administrative contact email.

        Does not require authentication.

        Returns an `instance dict`_.
        """
        return self.__instance()

    def __instance(self):
        """
        Internal, non-version-checking helper that does the same as instance()
        """
        return self.__api_request('GET', '/api/v1/instance/')

    @api_version("2.1.2", "2.1.2")
    def instance_activity(self):
        """
        Retrieve activity stats about the instance. May be disabled by the instance administrator - throws
        a MastodonNotFoundError in that case.
        
        Activity is returned for 12 weeks going back from the current week.
        
        Returns a list `activity dicts`_.
        """
        return self.__api_request('GET', '/api/v1/instance/activity')

    @api_version("2.1.2", "2.1.2")
    def instance_peers(self):
        """
        Retrieve the instances that this instance knows about. May be disabled by the instance administrator - throws
        a MastodonNotFoundError in that case.
        
        Returns a list of URL strings.
        """
        return self.__api_request('GET', '/api/v1/instance/peers')

    ###
    # Reading data: Timelines
    ##
    @api_version("1.0.0", "2.0.0")
    def timeline(self, timeline="home", max_id=None, since_id=None, limit=None):
        """
        Fetch statuses, most recent ones first. `timeline` can be 'home', 'local', 'public',
        'tag/hashtag' or 'list/id'. See the following functions documentation for what those do.
        Local hashtag timelines are supported via the `timeline_hashtag()`_ function.
        
        The default timeline is the "home" timeline.

        Returns a list of `toot dicts`_.
        """
        if max_id != None:
            max_id = self.__unpack_id(max_id)
        
        if since_id != None:
            since_id = self.__unpack_id(since_id)
            
        params_initial = locals()

        if timeline == "local":
            timeline = "public"
            params_initial['local'] = True

        params = self.__generate_params(params_initial, ['timeline'])
        url = '/api/v1/timelines/{0}'.format(timeline)
        return self.__api_request('GET', url, params)
    
    @api_version("1.0.0", "2.0.0")
    def timeline_home(self, max_id=None, since_id=None, limit=None):
        """
        Fetch the logged-in users home timeline (i.e. followed users and self).

        Returns a list of `toot dicts`_.
        """
        return self.timeline('home', max_id=max_id, since_id=since_id,
                             limit=limit)

    @api_version("1.0.0", "2.0.0")
    def timeline_local(self, max_id=None, since_id=None, limit=None):
        """
        Fetches the local / instance-wide timeline, not including replies.

        Returns a list of `toot dicts`_.
        """
        return self.timeline('local', max_id=max_id, since_id=since_id,
                             limit=limit)

    @api_version("1.0.0", "2.0.0")
    def timeline_public(self, max_id=None, since_id=None, limit=None):
        """
        Fetches the public / visible-network timeline, not including replies.

        Returns a list of `toot dicts`_.
        """
        return self.timeline('public', max_id=max_id, since_id=since_id,
                             limit=limit)

    @api_version("1.0.0", "2.0.0")    
    def timeline_hashtag(self, hashtag, local=False, max_id=None, since_id=None, limit=None):
        """
        Fetch a timeline of toots with a given hashtag. The hashtag parameter
        should not contain the leading #.

        Set `local` to True to retrieve only instance-local tagged posts.

        Returns a list of `toot dicts`_.
        """
        if hashtag.startswith("#"):
            raise MastodonIllegalArgumentError("Hashtag parameter should omit leading #")
            
        if max_id != None:
            max_id = self.__unpack_id(max_id)
        
        if since_id != None:
            since_id = self.__unpack_id(since_id)
            
        params_initial = locals()        
        
        if local == False:
            del params_initial['local']
        
        url = '/api/v1/timelines/tag/{0}'.format(hashtag)        
        params = self.__generate_params(params_initial, ['hashtag'])
        
        return self.__api_request('GET', url, params)

    @api_version("2.1.0", "2.1.0")
    def timeline_list(self, id, max_id=None, since_id=None, limit=None):
        """
        Fetches a timeline containing all the toots by users in a given list.

        Returns a list of `toot dicts`_.
        """
        id = self.__unpack_id(id)
        return self.timeline('list/{0}'.format(id), max_id=max_id, 
                             since_id=since_id, limit=limit)

    ###
    # Reading data: Statuses
    ###
    @api_version("1.0.0", "2.0.0")
    def status(self, id):
        """
        Fetch information about a single toot.

        Does not require authentication for publicly visible statuses.

        Returns a `toot dict`_.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/statuses/{0}'.format(str(id))
        return self.__api_request('GET', url)

    @api_version("1.0.0", "1.0.0")
    def status_card(self, id):
        """
        Fetch a card associated with a status. A card describes an object (such as an
        external video or link) embedded into a status.

        Does not require authentication for publicly visible statuses.

        Returns a `card dict`_.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/statuses/{0}/card'.format(str(id))
        return self.__api_request('GET', url)

    @api_version("1.0.0", "1.0.0")
    def status_context(self, id):
        """
        Fetch information about ancestors and descendants of a toot.

        Does not require authentication for publicly visible statuses.

        Returns a `context dict`_.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/statuses/{0}/context'.format(str(id))
        return self.__api_request('GET', url)

    @api_version("1.0.0", "2.1.0")
    def status_reblogged_by(self, id):
        """
        Fetch a list of users that have reblogged a status.

        Does not require authentication for publicly visible statuses.

        Returns a list of `user dicts`_.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/statuses/{0}/reblogged_by'.format(str(id))
        return self.__api_request('GET', url)

    @api_version("1.0.0", "2.1.0")
    def status_favourited_by(self, id):
        """
        Fetch a list of users that have favourited a status.

        Does not require authentication for publicly visible statuses.

        Returns a list of `user dicts`_.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/statuses/{0}/favourited_by'.format(str(id))
        return self.__api_request('GET', url)

    ###
    # Reading data: Notifications
    ###
    @api_version("1.0.0", "1.0.0")
    def notifications(self, id=None, max_id=None, since_id=None, limit=None):
        """
        Fetch notifications (mentions, favourites, reblogs, follows) for the logged-in
        user.

        Can be passed an `id` to fetch a single notification.

        Returns a list of `notification dicts`_.
        """
        if max_id != None:
            max_id = self.__unpack_id(max_id)
        
        if since_id != None:
            since_id = self.__unpack_id(since_id)
        
        if id is None:
            params = self.__generate_params(locals(), ['id'])
            return self.__api_request('GET', '/api/v1/notifications', params)
        else:
            id = self.__unpack_id(id)
            url = '/api/v1/notifications/{0}'.format(str(id))
            return self.__api_request('GET', url)

    ###
    # Reading data: Accounts
    ###
    @api_version("1.0.0", "1.0.0")
    def account(self, id):
        """
        Fetch account information by user `id`.

        Returns a `user dict`_.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/accounts/{0}'.format(str(id))
        return self.__api_request('GET', url)
    
    @api_version("1.0.0", "2.1.0")
    def account_verify_credentials(self):
        """
        Fetch logged-in user's account information.

        Returns a `user dict`_ (Starting from 2.1.0, with an additional "source" field).
        """
        return self.__api_request('GET', '/api/v1/accounts/verify_credentials')

    @api_version("1.0.0", "2.0.0")
    def account_statuses(self, id, only_media=False, pinned=False, exclude_replies=False, max_id=None, since_id=None, limit=None):
        """
        Fetch statuses by user `id`. Same options as `timeline()`_ are permitted.
        Returned toots are from the perspective of the logged-in user, i.e.
        all statuses visible to the logged-in user (including DMs) are
        included.

        If `only_media` is set, return only statuses with media attachments.
        If `pinned` is set, return only statuses that have been pinned. Note that 
        as of Mastodon 2.1.0, this only works properly for instance-local users.
        If `exclude_replies` is set, filter out all statuses that are replies.

        Returns a list of `toot dicts`_.
        """
        id = self.__unpack_id(id)
        if max_id != None:
            max_id = self.__unpack_id(max_id)
        
        if since_id != None:
            since_id = self.__unpack_id(since_id)
        
        params = self.__generate_params(locals(), ['id'])
        if pinned == False:
            del params["pinned"]
        if only_media == False:
            del params["only_media"]
        if exclude_replies == False:
            del params["exclude_replies"]
        
        url = '/api/v1/accounts/{0}/statuses'.format(str(id))
        return self.__api_request('GET', url, params)

    @api_version("1.0.0", "2.1.0")
    def account_following(self, id, max_id=None, since_id=None, limit=None):
        """
        Fetch users the given user is following.

        Returns a list of `user dicts`_.
        """
        id = self.__unpack_id(id)
        if max_id != None:
            max_id = self.__unpack_id(max_id)
        
        if since_id != None:
            since_id = self.__unpack_id(since_id)
            
        params = self.__generate_params(locals(), ['id'])
        url = '/api/v1/accounts/{0}/following'.format(str(id))
        return self.__api_request('GET', url, params)

    @api_version("1.0.0", "2.1.0")
    def account_followers(self, id, max_id=None, since_id=None, limit=None):
        """
        Fetch users the given user is followed by.

        Returns a list of `user dicts`_.
        """
        id = self.__unpack_id(id)
        if max_id != None:
            max_id = self.__unpack_id(max_id)
        
        if since_id != None:
            since_id = self.__unpack_id(since_id)
            
        params = self.__generate_params(locals(), ['id'])
        url = '/api/v1/accounts/{0}/followers'.format(str(id))
        return self.__api_request('GET', url, params)
    
    @api_version("1.0.0", "1.4.0")
    def account_relationships(self, id):
        """
        Fetch relationship (following, followed_by, blocking, follow requested) of 
        the logged in user to a given account. `id` can be a list.

        Returns a list of `relationship dicts`_.
        """
        id = self.__unpack_id(id)
        params = self.__generate_params(locals())
        return self.__api_request('GET', '/api/v1/accounts/relationships',
                                  params)

    @api_version("1.0.0", "2.1.0")
    def account_search(self, q, limit=None):
        """
        Fetch matching accounts. Will lookup an account remotely if the search term is
        in the username@domain format and not yet in the database.

        Returns a list of `user dicts`_.
        """
        params = self.__generate_params(locals())
        return self.__api_request('GET', '/api/v1/accounts/search', params)

    @api_version("2.1.0", "2.1.0")
    def account_lists(self, id):
        """
        Get all of the logged in users lists which the specified user is
        a member of.
        
        Returns a list of `list dicts`_.
        """
        params = self.__generate_params(locals(), ['id'])
        url = '/api/v1/accounts/{0}/lists'.format(str(id))
        return self.__api_request('GET', url, params)
    
    ###
    # Reading data: Searching
    ###
    @api_version("1.1.0", "2.1.0")
    def search(self, q, resolve=False):
        """
        Fetch matching hashtags, accounts and statuses. Will search federated
        instances if resolve is True.

        Returns a `search result dict`_.
        """
        params = self.__generate_params(locals())
        return self.__api_request('GET', '/api/v1/search', params)

    ###
    # Reading data: Lists
    ###
    @api_version("2.1.0", "2.1.0")
    def lists(self):
        """
        Fetch a list of all the Lists by the logged-in user.
        
        Returns a list of `list dicts`_.
        """
        return self.__api_request('GET', '/api/v1/lists')

    @api_version("2.1.0", "2.1.0")
    def list(self, id):
        """
        Fetch info about a specific list.
        
        Returns a `list dict`_.
        """
        id = self.__unpack_id(id)        
        return self.__api_request('GET', '/api/v1/lists/{0}'.format(id))

    @api_version("2.1.0", "2.1.0")
    def list_accounts(self, id, max_id=None, since_id=None, limit=None):
        """
        Get the accounts that are on the given list. A `limit` of 0 can
        be specified to get all accounts without pagination.
        
        Returns a list of `user dicts`_.
        """
        id = self.__unpack_id(id)
        
        if max_id != None:
            max_id = self.__unpack_id(max_id)
        
        if since_id != None:
            since_id = self.__unpack_id(since_id)
        
        params = self.__generate_params(locals(), ['id']) 
        return self.__api_request('GET', '/api/v1/lists/{0}/accounts'.format(id))

    ###
    # Reading data: Mutes and Blocks
    ###
    @api_version("1.1.0", "2.1.0")    
    def mutes(self, max_id=None, since_id=None, limit=None):
        """
        Fetch a list of users muted by the logged-in user.

        Returns a list of `user dicts`_.
        """
        if max_id != None:
            max_id = self.__unpack_id(max_id)
        
        if since_id != None:
            since_id = self.__unpack_id(since_id)
            
        params = self.__generate_params(locals())
        return self.__api_request('GET', '/api/v1/mutes', params)

    @api_version("1.0.0", "2.1.0")
    def blocks(self, max_id=None, since_id=None, limit=None):
        """
        Fetch a list of users blocked by the logged-in user.

        Returns a list of `user dicts`_.
        """
        if max_id != None:
            max_id = self.__unpack_id(max_id)
        
        if since_id != None:
            since_id = self.__unpack_id(since_id)
            
        params = self.__generate_params(locals())
        return self.__api_request('GET', '/api/v1/blocks', params)

    ###
    # Reading data: Reports
    ###
    @api_version("1.1.0", "1.1.0")
    def reports(self):
        """
        Fetch a list of reports made by the logged-in user.

        Returns a list of `report dicts`_.
        
        Warning: According to the official API documentation, this
        method is to be treated as not finalized as of Mastodon 2.1.0.
        """
        return self.__api_request('GET', '/api/v1/reports')

    ###
    # Reading data: Favourites
    ###
    @api_version("1.0.0", "2.0.0")
    def favourites(self, max_id=None, since_id=None, limit=None):
        """
        Fetch the logged-in user's favourited statuses.

        Returns a list of `toot dicts`_.
        """
        if max_id != None:
            max_id = self.__unpack_id(max_id)
        
        if since_id != None:
            since_id = self.__unpack_id(since_id)
            
        params = self.__generate_params(locals())
        return self.__api_request('GET', '/api/v1/favourites', params)

    ###
    # Reading data: Follow requests
    ###
    @api_version("1.0.0", "2.1.0")
    def follow_requests(self, max_id=None, since_id=None, limit=None):
        """
        Fetch the logged-in user's incoming follow requests.

        Returns a list of `user dicts`_.
        """
        if max_id != None:
            max_id = self.__unpack_id(max_id)
        
        if since_id != None:
            since_id = self.__unpack_id(since_id)
            
        params = self.__generate_params(locals())
        return self.__api_request('GET', '/api/v1/follow_requests', params)

    ###
    # Reading data: Domain blocks
    ###
    @api_version("1.4.0", "1.4.0")
    def domain_blocks(self, max_id=None, since_id=None, limit=None):
        """
        Fetch the logged-in user's blocked domains.

        Returns a list of blocked domain URLs (as strings, without protocol specifier).
        """
        if max_id != None:
            max_id = self.__unpack_id(max_id)
        
        if since_id != None:
            since_id = self.__unpack_id(since_id)
            
        params = self.__generate_params(locals())
        return self.__api_request('GET', '/api/v1/domain_blocks', params)

    ###
    # Reading data: Emoji
    ###
    @api_version("2.1.0", "2.1.0")
    def custom_emojis(self):
        """
        Fetch the list of custom emoji the instance has installed.

        Does not require authentication.

        Returns a list of `emoji dicts`_.
        
        """
        return self.__api_request('GET', '/api/v1/custom_emojis')

    ###
    # Writing data: Statuses
    ###
    @api_version("1.0.0", "2.0.0")
    def status_post(self, status, in_reply_to_id=None, media_ids=None,
                    sensitive=False, visibility='', spoiler_text=None):
        """
        Post a status. Can optionally be in reply to another status and contain
        media.
        
        `media_ids` should be a list. (If it's not, the function will turn it
        into one.) It can contain up to four pieces of media (uploaded via 
        `media_post()`_). `media_ids` can also be the `media dicts`_ returned 
        by `media_post()`_ - they are unpacked automatically.

        The `sensitive` boolean decides whether or not media attached to the post
        should be marked as sensitive, which hides it by default on the Mastodon
        web front-end.

        The visibility parameter is a string value and matches the visibility
        option on the /api/v1/status POST API endpoint. It accepts any of:
        'direct' - post will be visible only to mentioned users
        'private' - post will be visible only to followers
        'unlisted' - post will be public but not appear on the public timeline
        'public' - post will be public

        If not passed in, visibility defaults to match the current account's
        default-privacy setting (starting with Mastodon version 1.6) or its
        locked setting - private if the account is locked, public otherwise
        (for Mastodon versions lower than 1.6).

        The `spoiler_text` parameter is a string to be shown as a warning before
        the text of the status.  If no text is passed in, no warning will be
        displayed.

        Returns a `toot dict`_ with the new status.
        """
        if in_reply_to_id != None:
            in_reply_to_id = self.__unpack_id(in_reply_to_id)

        params_initial = locals()

        params_initial['visibility'] = params_initial['visibility'].lower()
        valid_visibilities = ['private', 'public', 'unlisted', 'direct', '']
        if params_initial['visibility'] not in valid_visibilities:
            raise ValueError(
                f'Invalid visibility value! Acceptable values are {valid_visibilities}'
            )

        if params_initial['sensitive'] is False:
            del [params_initial['sensitive']]

        if media_ids is not None:
            try:
                media_ids_proper = []
                if not isinstance(media_ids, (list, tuple)):
                    media_ids = [media_ids]
                for media_id in media_ids:
                    if isinstance(media_id, dict):
                        media_ids_proper.append(media_id["id"])
                    else:
                        media_ids_proper.append(media_id)
            except Exception as e:
                raise MastodonIllegalArgumentError(f"Invalid media dict: {e}")

            params_initial["media_ids"] = media_ids_proper

        params = self.__generate_params(params_initial)
        return self.__api_request('POST', '/api/v1/statuses', params)

    @api_version("1.0.0", "2.0.0")
    def toot(self, status):
        """
        Synonym for `status_post()`_ that only takes the status text as input.

        Usage in production code is not recommended.

        Returns a `toot dict`_ with the new status.
        """
        return self.status_post(status)

    @api_version("1.0.0", "1.0.0")
    def status_delete(self, id):
        """
        Delete a status
        """
        id = self.__unpack_id(id)
        url = '/api/v1/statuses/{0}'.format(str(id))
        self.__api_request('DELETE', url)

    @api_version("1.0.0", "2.0.0")
    def status_reblog(self, id):
        """
        Reblog a status.

        Returns a `toot dict`_ with a new status that wraps around the reblogged one.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/statuses/{0}/reblog'.format(str(id))
        return self.__api_request('POST', url)

    @api_version("1.0.0", "2.0.0")
    def status_unreblog(self, id):
        """
        Un-reblog a status.

        Returns a `toot dict`_ with the status that used to be reblogged.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/statuses/{0}/unreblog'.format(str(id))
        return self.__api_request('POST', url)

    @api_version("1.0.0", "2.0.0")
    def status_favourite(self, id):
        """
        Favourite a status.

        Returns a `toot dict`_ with the favourited status.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/statuses/{0}/favourite'.format(str(id))
        return self.__api_request('POST', url)

    @api_version("1.0.0", "2.0.0")
    def status_unfavourite(self, id):
        """
        Un-favourite a status.

        Returns a `toot dict`_ with the un-favourited status.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/statuses/{0}/unfavourite'.format(str(id))
        return self.__api_request('POST', url)
    
    @api_version("1.4.0", "2.0.0")
    def status_mute(self, id):
        """
        Mute notifications for a status.

        Returns a `toot dict`_ with the now muted status
        """
        id = self.__unpack_id(id)
        url = '/api/v1/statuses/{0}/mute'.format(str(id))
        return self.__api_request('POST', url)

    @api_version("1.4.0", "2.0.0")
    def status_unmute(self, id):
        """
        Unmute notifications for a status.

        Returns a `toot dict`_ with the status that used to be muted.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/statuses/{0}/unmute'.format(str(id))
        return self.__api_request('POST', url)

    ###
    # Writing data: Notifications
    ###
    @api_version("1.0.0", "1.0.0")
    def notifications_clear(self):
        """
        Clear out a users notifications
        """
        self.__api_request('POST', '/api/v1/notifications/clear')


    @api_version("1.3.0", "1.3.0")
    def notifications_dismiss(self, id):
        """
        Deletes a single notification
        """
        id = self.__unpack_id(id)
        params = self.__generate_params(locals())
        self.__api_request('POST', '/api/v1/notifications/dismiss', params)

    ###
    # Writing data: Accounts
    ###
    @api_version("1.0.0", "1.4.0")
    def account_follow(self, id):
        """
        Follow a user.

        Returns a `relationship dict`_ containing the updated relationship to the user.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/accounts/{0}/follow'.format(str(id))
        return self.__api_request('POST', url)

    @api_version("1.0.0", "2.1.0")
    def follows(self, uri):
        """
        Follow a remote user by uri (username@domain).

        Returns a `user dict`_.
        """
        params = self.__generate_params(locals())
        return self.__api_request('POST', '/api/v1/follows', params)

    @api_version("1.0.0", "1.4.0")
    def account_unfollow(self, id):
        """
        Unfollow a user.

        Returns a `relationship dict`_ containing the updated relationship to the user.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/accounts/{0}/unfollow'.format(str(id))
        return self.__api_request('POST', url)

    @api_version("1.0.0", "1.4.0")
    def account_block(self, id):
        """
        Block a user.

        Returns a `relationship dict`_ containing the updated relationship to the user.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/accounts/{0}/block'.format(str(id))
        return self.__api_request('POST', url)

    @api_version("1.0.0", "1.4.0")
    def account_unblock(self, id):
        """
        Unblock a user.

        Returns a `relationship dict`_ containing the updated relationship to the user.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/accounts/{0}/unblock'.format(str(id))
        return self.__api_request('POST', url)

    @api_version("1.1.0", "1.4.0")
    def account_mute(self, id):
        """
        Mute a user.

        Returns a `relationship dict`_ containing the updated relationship to the user.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/accounts/{0}/mute'.format(str(id))
        return self.__api_request('POST', url)

    @api_version("1.1.0", "1.4.0")
    def account_unmute(self, id):
        """
        Unmute a user.

        Returns a `relationship dict`_ containing the updated relationship to the user.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/accounts/{0}/unmute'.format(str(id))
        return self.__api_request('POST', url)

    @api_version("1.1.1", "2.1.0")
    def account_update_credentials(self, display_name=None, note=None,
                                   avatar=None, header=None):
        """
        Update the profile for the currently logged-in user.

        'note' is the user's bio.

        'avatar' and 'header' are images encoded in base64, prepended by a content-type
        (for example: 'data:image/png;base64,iVBORw0KGgoAAAA[...]')
        
        Returns the updated `user dict` of the logged-in user.
        """
        params = self.__generate_params(locals())
        return self.__api_request('PATCH', '/api/v1/accounts/update_credentials', params)

    ###
    # Writing data: Lists
    ###
    @api_version("2.1.0", "2.1.0")
    def list_create(self, title):
        """
        Create a new list with the given `title`.
        
        Returns the `list dict`_ of the created list.
        """
        params = self.__generate_params(locals())
        return self.__api_request('POST', '/api/v1/lists', params)
    
    @api_version("2.1.0", "2.1.0")
    def list_update(self, id, title):
        """
        Update info about a list, where "info" is really the lists `title`.
        
        Returns the `list dict`_ of the modified list.
        """
        id = self.__unpack_id(id)
        params = self.__generate_params(locals(), ['id'])
        return self.__api_request('PUT', '/api/v1/lists/{0}'.format(id), params)
    
    @api_version("2.1.0", "2.1.0")
    def list_delete(self, id):
        """
        Delete a list.
        """
        id = self.__unpack_id(id)
        self.__api_request('DELETE', '/api/v1/lists/{0}'.format(id))
    
    @api_version("2.1.0", "2.1.0")
    def list_accounts_add(self, id, account_ids):
        """
        Add the account(s) given in `account_ids` to the list.
        """
        id = self.__unpack_id(id)
        
        if not isinstance(account_ids, list):
            account_ids = [account_ids]
        account_ids = list(map(lambda x: self.__unpack_id(x), account_ids))
        
        params = self.__generate_params(locals(), ['id'])        
        self.__api_request('POST', '/api/v1/lists/{0}/accounts'.format(id), params)
        
    @api_version("2.1.0", "2.1.0")
    def list_accounts_delete(self, id, account_ids):
        """
        Remove the account(s) given in `account_ids` from the list.
        """
        id = self.__unpack_id(id)
        
        if not isinstance(account_ids, list):
            account_ids = [account_ids]
        account_ids = list(map(lambda x: self.__unpack_id(x), account_ids))
        
        params = self.__generate_params(locals(), ['id'])        
        self.__api_request('DELETE', '/api/v1/lists/{0}/accounts'.format(id), params)
        
    ###
    # Writing data: Reports
    ###
    @api_version("1.1.0", "1.1.0")
    def report(self, account_id, status_ids, comment):
        """
        Report statuses to the instances administrators.

        Accepts a list of toot IDs associated with the report, and a comment.

        Returns a `report dict`_.
        """
        account_id = self.__unpack_id(account_id)
        
        if not isinstance(status_ids, list):
            status_ids = [status_ids]
        status_ids = list(map(lambda x: self.__unpack_id(x), status_ids))
        
        params = self.__generate_params(locals())
        return self.__api_request('POST', '/api/v1/reports/', params)

    ###
    # Writing data: Follow requests
    ###
    @api_version("1.0.0", "1.0.0")
    def follow_request_authorize(self, id):
        """
        Accept an incoming follow request.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/follow_requests/{0}/authorize'.format(str(id))
        self.__api_request('POST', url)

    @api_version("1.0.0", "1.0.0")
    def follow_request_reject(self, id):
        """
        Reject an incoming follow request.
        """
        id = self.__unpack_id(id)
        url = '/api/v1/follow_requests/{0}/reject'.format(str(id))
        self.__api_request('POST', url)

    ###
    # Writing data: Media
    ###
    @api_version("1.0.0", "2.0.0")
    def media_post(self, media_file, mime_type=None, description=None):
        """
        Post an image. `media_file` can either be image data or
        a file name. If image data is passed directly, the mime
        type has to be specified manually, otherwise, it is
        determined from the file name.

        Throws a `MastodonIllegalArgumentError` if the mime type of the
        passed data or file can not be determined properly.

        Returns a `media dict`_. This contains the id that can be used in
        status_post to attach the media file to a toot.
        """
        if mime_type is None and os.path.isfile(media_file):
            mime_type = mimetypes.guess_type(media_file)[0]
            media_file = open(media_file, 'rb')

        if mime_type is None:
            raise MastodonIllegalArgumentError('Could not determine mime type'
                                               ' or data passed directly '
                                               'without mime type.')

        random_suffix = ''.join(random.choice(string.ascii_uppercase + string.digits) for _ in range(10))
        file_name = f"mastodonpyupload_{str(time.time())}_{random_suffix}{mimetypes.guess_extension(mime_type)}"

        media_file_description = (file_name, media_file, mime_type)
        return self.__api_request('POST', '/api/v1/media',
                                  files={'file': media_file_description},
                                  params={'description': description})

    ###
    # Writing data: Domain blocks
    ###
    @api_version("1.4.0", "1.4.0")
    def domain_block(self, domain=None):
        """
        Add a block for all statuses originating from the specified domain for the logged-in user.
        """
        params = self.__generate_params(locals())
        self.__api_request('POST', '/api/v1/domain_blocks', params)

    @api_version("1.4.0", "1.4.0")
    def domain_unblock(self, domain=None):
        """
        Remove a domain block for the logged-in user.
        """
        params = self.__generate_params(locals())
        self.__api_request('DELETE', '/api/v1/domain_blocks', params)

    ###
    # Pagination
    ###
    def fetch_next(self, previous_page):
        """
        Fetches the next page of results of a paginated request. Pass in the
        previous page in its entirety, or the pagination information dict
        returned as a part of that pages last status ('_pagination_next').

        Returns the next page or None if no further data is available.
        """
        if isinstance(previous_page, list) and len(previous_page) != 0:
            if '_pagination_next' in previous_page[-1]:
                params = copy.deepcopy(previous_page[-1]['_pagination_next'])
            else:
                return None
        else:
            params = copy.deepcopy(previous_page)

        method = params['_pagination_method']
        del params['_pagination_method']

        endpoint = params['_pagination_endpoint']
        del params['_pagination_endpoint']

        return self.__api_request(method, endpoint, params)

    def fetch_previous(self, next_page):
        """
        Fetches the previous page of results of a paginated request. Pass in the
        previous page in its entirety, or the pagination information dict
        returned as a part of that pages first status ('_pagination_prev').

        Returns the previous page or None if no further data is available.
        """
        if isinstance(next_page, list) and len(next_page) != 0:
            if '_pagination_prev' in next_page[0]:
                params = copy.deepcopy(next_page[0]['_pagination_prev'])
            else:
                return None
        else:
            params = copy.deepcopy(next_page)

        method = params['_pagination_method']
        del params['_pagination_method']

        endpoint = params['_pagination_endpoint']
        del params['_pagination_endpoint']

        return self.__api_request(method, endpoint, params)

    def fetch_remaining(self, first_page):
        """
        Fetches all the remaining pages of a paginated request starting from a
        first page and returns the entire set of results (including the first page
        that was passed in) as a big list.

        Be careful, as this might generate a lot of requests, depending on what you are
        fetching, and might cause you to run into rate limits very quickly.
        """
        first_page = copy.deepcopy(first_page)

        all_pages = []
        current_page = first_page
        while current_page is not None and len(current_page) > 0:
            all_pages.extend(current_page)
            current_page = self.fetch_next(current_page)

        return all_pages

    ###
    # Streaming
    ###
    @api_version("1.1.0", "1.4.2")    
    def stream_user(self, listener, async=False):
        """
        Streams events that are relevant to the authorized user, i.e. home
        timeline and notifications.
        """
        return self.__stream('/api/v1/streaming/user', listener, async=async)

    @api_version("1.1.0", "1.4.2")
    def stream_public(self, listener, async=False):
        """
        Streams public events.
        """
        return self.__stream('/api/v1/streaming/public', listener, async=async)

    @api_version("1.1.0", "1.4.2")
    def stream_local(self, listener, async=False):
        """
        Streams local public events.
        """
        return self.__stream('/api/v1/streaming/public/local', listener, async=async)

    @api_version("1.1.0", "1.4.2")
    def stream_hashtag(self, tag, listener, async=False):
        """
        Stream for all public statuses for the hashtag 'tag' seen by the connected
        instance.
        """
        if tag.startswith("#"):
            raise MastodonIllegalArgumentError("Tag parameter should omit leading #")
        return self.__stream(
            f"/api/v1/streaming/hashtag?tag={tag}", listener, async=async
        )

    @api_version("2.1.0", "2.1.0")
    def stream_list(self, id, listener, async=False):
        """
        Stream events for the current user, restricted to accounts on the given
        list. 
        """
        id =  self.__unpack_id(id)
        return self.__stream(
            f"/api/v1/streaming/list?list={id}", listener, async=async
        )
    
    ###
    # Internal helpers, dragons probably
    ###
    def __datetime_to_epoch(self, date_time):
        """
        Converts a python datetime to unix epoch, accounting for
        time zones and such.

        Assumes UTC if timezone is not given.
        """
        date_time_utc = None
        if date_time.tzinfo is None:
            date_time_utc = date_time.replace(tzinfo=pytz.utc)
        else:
            date_time_utc = date_time.astimezone(pytz.utc)

        epoch_utc = datetime.datetime.utcfromtimestamp(0).replace(tzinfo=pytz.utc)

        return (date_time_utc - epoch_utc).total_seconds()

    @staticmethod
    def __json_allow_dict_attrs(json_object):
        """
        Makes it possible to use attribute notation to access a dicts
        elements, while still allowing the dict to act as a dict.
        """
        if isinstance(json_object, dict):
            return AttribAccessDict(json_object)
        return json_object

    @staticmethod
    def __json_date_parse(json_object):
        """
        Parse dates in certain known json fields, if possible.
        """
        known_date_fields = ["created_at", "week"]
        for k, v in json_object.items():
            if k in known_date_fields:
                try:
                    if isinstance(v, int):
                        json_object[k] = datetime.datetime.fromtimestamp(v, pytz.utc)
                    else:
                        json_object[k] = dateutil.parser.parse(v)
                except:
                    raise MastodonAPIError('Encountered invalid date.')
        return json_object

    @staticmethod
    def __json_strnum_to_bignum(json_object):
        """
        Converts json string numerals to native python bignums.
        """
        for key in ('id', 'week', 'in_reply_to_id', 'in_reply_to_account_id', 'logins', 'registrations', 'statuses'):
            if (key in json_object and isinstance(json_object[key], six.text_type)):
                try:
                    json_object[key] = int(json_object[key])
                except ValueError:
                    pass

        return json_object
    
    @staticmethod
    def __json_hooks(json_object):
        json_object = Mastodon.__json_strnum_to_bignum(json_object)        
        json_object = Mastodon.__json_date_parse(json_object)
        json_object = Mastodon.__json_allow_dict_attrs(json_object)
        return json_object

    def __api_request(self, method, endpoint, params={}, files={}, do_ratelimiting=True):
        """
        Internal API request helper.
        """
        response = None
        headers = None
        remaining_wait = 0
        # "pace" mode ratelimiting: Assume constant rate of requests, sleep a little less long than it
        # would take to not hit the rate limit at that request rate.
        if do_ratelimiting and self.ratelimit_method == "pace":
            if self.ratelimit_remaining == 0:
                to_next = self.ratelimit_reset - time.time()
                if to_next > 0:
                    # As a precaution, never sleep longer than 5 minutes
                    to_next = min(to_next, 5 * 60)
                    time.sleep(to_next)
            else:
                time_waited = time.time() - self.ratelimit_lastcall
                time_wait = float(self.ratelimit_reset - time.time()) / float(self.ratelimit_remaining)
                remaining_wait = time_wait - time_waited

            if remaining_wait > 0:
                to_next = remaining_wait / self.ratelimit_pacefactor
                to_next = min(to_next, 5 * 60)
                time.sleep(to_next)

        # Generate request headers
        if self.access_token is not None:
            headers = {'Authorization': 'Bearer ' + self.access_token}

        if self.debug_requests:
            print('Mastodon: Request to endpoint "' + endpoint + '" using method "' + method + '".')
            print('Parameters: ' + str(params))
            print('Headers: ' + str(headers))
            print('Files: ' + str(files))

        # Make request
        request_complete = False
        while not request_complete:
            request_complete = True

            response_object = None
            try:
                kwargs = dict(headers=headers, files=files,
                              timeout=self.request_timeout)
                if method == 'GET':
                    kwargs['params'] = params
                else:
                    kwargs['data'] = params

                response_object = requests.request(
                        method, self.api_base_url + endpoint, **kwargs)
            except Exception as e:
                raise MastodonNetworkError("Could not complete request: %s" % e)

            if response_object is None:
                raise MastodonIllegalArgumentError("Illegal request.")

            # Parse rate limiting headers
            if 'X-RateLimit-Remaining' in response_object.headers and do_ratelimiting:
                self.ratelimit_remaining = int(response_object.headers['X-RateLimit-Remaining'])
                self.ratelimit_limit = int(response_object.headers['X-RateLimit-Limit'])

                try:
                    ratelimit_reset_datetime = dateutil.parser.parse(response_object.headers['X-RateLimit-Reset'])
                    self.ratelimit_reset = self.__datetime_to_epoch(ratelimit_reset_datetime)

                    # Adjust server time to local clock
                    if 'Date' in response_object.headers:
                        server_time_datetime = dateutil.parser.parse(response_object.headers['Date'])
                        server_time = self.__datetime_to_epoch(server_time_datetime)
                        server_time_diff = time.time() - server_time
                        self.ratelimit_reset += server_time_diff
                        self.ratelimit_lastcall = time.time()
                except Exception as e:
                    raise MastodonRatelimitError("Rate limit time calculations failed: %s" % e)

            # Handle response
            if self.debug_requests:
                print('Mastodon: Response received with code ' + str(response_object.status_code) + '.')
                print('response headers: ' + str(response_object.headers))
                print('Response text content: ' + str(response_object.text))

            if not response_object.ok:
                try:
                    response = response_object.json(object_hook=self.__json_hooks)
                    if not isinstance(response, dict) or 'error' not in response:
                        error_msg = None
                    error_msg = response['error']
                except ValueError:
                    error_msg = None

                # Handle rate limiting
                if response_object.status_code == 429:
                    if self.ratelimit_method == 'throw' or not do_ratelimiting:
                        raise MastodonRatelimitError('Hit rate limit.')
                    elif self.ratelimit_method in ('wait', 'pace'):
                        to_next = self.ratelimit_reset - time.time()
                        if to_next > 0:
                            # As a precaution, never sleep longer than 5 minutes
                            to_next = min(to_next, 5 * 60)
                            time.sleep(to_next)
                            request_complete = False
                            continue

                if response_object.status_code == 404:
                    ex_type = MastodonNotFoundError
                    if not error_msg:
                        error_msg = 'Endpoint not found.'
                        # this is for compatibility with older versions
                        # which raised MastodonAPIError('Endpoint not found.')
                        # on any 404
                elif response_object.status_code == 401:
                    ex_type = MastodonUnauthorizedError
                else:
                    ex_type = MastodonAPIError

                raise ex_type(
                        'Mastodon API returned error',
                        response_object.status_code,
                        response_object.reason,
                        error_msg)

            try:
                response = response_object.json(object_hook=self.__json_hooks)
            except:
                raise MastodonAPIError(
                    "Could not parse response as JSON, response code was %s, "
                    "bad json content was '%s'" % (response_object.status_code,
                                                   response_object.content))

            # Parse link headers
            if isinstance(response, list) and \
                    'Link' in response_object.headers and \
                    response_object.headers['Link'] != "":
                tmp_urls = requests.utils.parse_header_links(
                    response_object.headers['Link'].rstrip('>').replace('>,<', ',<'))
                for url in tmp_urls:
                    if 'rel' not in url:
                        continue

                    if url['rel'] == 'next':
                        # Be paranoid and extract max_id specifically
                        next_url = url['url']
                        matchgroups = re.search(r"max_id=([0-9]*)", next_url)

                        if matchgroups:
                            next_params = copy.deepcopy(params)
                            next_params['_pagination_method'] = method
                            next_params['_pagination_endpoint'] = endpoint
                            next_params['max_id'] = int(matchgroups.group(1))
                            if "since_id" in next_params:
                                del next_params['since_id']
                            response[-1]['_pagination_next'] = next_params

                    if url['rel'] == 'prev':
                        # Be paranoid and extract since_id specifically
                        prev_url = url['url']
                        matchgroups = re.search(r"since_id=([0-9]*)", prev_url)

                        if matchgroups:
                            prev_params = copy.deepcopy(params)
                            prev_params['_pagination_method'] = method
                            prev_params['_pagination_endpoint'] = endpoint
                            prev_params['since_id'] = int(matchgroups.group(1))
                            if "max_id" in prev_params:
                                del prev_params['max_id']
                            response[0]['_pagination_prev'] = prev_params


        return response

    def __stream(self, endpoint, listener, params={}, async=False):
        """
        Internal streaming API helper.

        Returns a handle to the open connection that the user can close if they
        wish to terminate it.
        """

        # Check if we have to redirect
        instance = self.instance()
        if "streaming_api" in instance["urls"] and instance["urls"]["streaming_api"] != self.api_base_url:
            # This is probably a websockets URL, which is really for the browser, but requests can't handle it
            # So we do this below to turn it into an HTTPS or HTTP URL
            parse = urlparse(instance["urls"]["streaming_api"])
            if parse.scheme == 'wss':
                url = "https://" + parse.netloc
            elif parse.scheme == 'ws':
                url = "http://" + parse.netloc
            else:
                raise MastodonAPIError(
                        "Could not parse streaming api location returned from server: {}.".format(
                            instance["urls"]["streaming_api"]))
        else:
            url = self.api_base_url

        # The streaming server can't handle two slashes in a path, so remove trailing slashes
        if url[-1] == '/':
            url = url[:-1]

        headers = {"Authorization": "Bearer " + self.access_token}
        connection = requests.get(url + endpoint, headers = headers, data = params, stream = True)

        if connection.status_code != 200:
            raise MastodonNetworkError("Could not connect to streaming server: %s" % connection.reason)

        class __stream_handle():
            def __init__(self, connection):
                self.closed = False
                self.connection = connection

            def close(self):
                self.closed = True
                self.connection.close()

            def is_alive(self):
                return self._thread.is_alive()

            def _threadproc(self):
                self._thread = threading.current_thread()
                with closing(connection) as r:
                    try:
                        listener.handle_stream(r)
                    except AttributeError as e:
                        if not self.closed:
                            raise e
                return 0

        handle = __stream_handle(connection)

        if async:
            t = threading.Thread(args=(), daemon = True, target=handle._threadproc)
            t.start()
            return handle
        else:
            # Blocking, never returns (can only leave via exception)
            with closing(connection) as r:
                listener.handle_stream(r)

    def __generate_params(self, params, exclude=[]):
        """
        Internal named-parameters-to-dict helper.

        Note for developers: If called with locals() as params,
        as is the usual practice in this code, the __generate_params call
        (or at least the locals() call) should generally be the first thing
        in your function.
        """
        params = dict(params)

        del params['self']
        param_keys = list(params.keys())
        for key in param_keys:
            if params[key] is None or key in exclude:
                del params[key]

        param_keys = list(params.keys())
        for key in param_keys:
            if isinstance(params[key], list):
                params[key + "[]"] = params[key]
                del params[key]

        return params
    
    def __unpack_id(self, id):
        """
        Internal object-to-id converter
        
        Checks if id is a dict that contains id and
        returns the id inside, otherwise just returns
        the id straight.
        """
        if isinstance(id, dict) and "id" in id:
            return id["id"]
        else:
            return id
        
    def __get_token_expired(self):
        """Internal helper for oauth code"""
        return self._token_expired < datetime.datetime.now()

    def __set_token_expired(self, value):
        """Internal helper for oauth code"""
        self._token_expired = datetime.datetime.now() + datetime.timedelta(seconds=value)
        return

    def __get_refresh_token(self):
        """Internal helper for oauth code"""
        return self._refresh_token

    def __set_refresh_token(self, value):
        """Internal helper for oauth code"""
        self._refresh_token = value
        return
    
    @staticmethod
    def __protocolize(base_url):
        """Internal add-protocol-to-url helper"""
        if not base_url.startswith("http://") and not base_url.startswith("https://"):
            base_url = "https://" + base_url

        # Some API endpoints can't handle extra /'s in path requests
        base_url = base_url.rstrip("/")
        return base_url


##
# Exceptions
##
class MastodonError(Exception):
    """Base class for Mastodon.py exceptions"""

class MastodonVersionError(MastodonError):
    """Raised when a function is called that the version of Mastodon for which
       Mastodon.py was instantiated does not support"""

class MastodonIllegalArgumentError(ValueError, MastodonError):
    """Raised when an incorrect parameter is passed to a function"""
    pass


class MastodonIOError(IOError, MastodonError):
    """Base class for Mastodon.py I/O errors"""


class MastodonFileNotFoundError(MastodonIOError):
    """Raised when a file requested to be loaded can not be opened"""
    pass


class MastodonNetworkError(MastodonIOError):
    """Raised when network communication with the server fails"""
    pass


class MastodonAPIError(MastodonError):
    """Raised when the mastodon API generates a response that cannot be handled"""
    pass

class MastodonNotFoundError(MastodonAPIError):
    """Raised when the mastodon API returns a 404 Not Found error"""
    pass

class MastodonUnauthorizedError(MastodonAPIError):
    """Raised when the mastodon API returns a 401 Unauthorized error

       This happens when an OAuth token is invalid or has been revoked."""
    pass


class MastodonRatelimitError(MastodonError):
    """Raised when rate limiting is set to manual mode and the rate limit is exceeded"""
    pass

class MastodonMalformedEventError(MastodonError):
    """Raised when the server-sent event stream is malformed"""
    pass
