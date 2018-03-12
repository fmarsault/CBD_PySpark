# coding: utf-8

import socket
import sys
import requests
import requests_oauthlib
import json
from w3lib.html import replace_entities, replace_tags
import mastodon
from mastodon import Mastodon

# ACCESS_TOKEN = "c51d68a4aec2e06ac669e5d204db26536e23ba70c58eef37f9526679359b3f64"
# ACCESS_SECRET = ""
# CONSUMER_KEY = "18726323135f57342de24483b1f59a384a8d1a21f7571ac81a120184ac9daac7"
# CONSUMER_SECRET = "d1029c6d5d0d3c05884e192c4d1108ccbf3b826955201ab10d479ec5ce563044"
# my_auth = requests_oauthlib.OAuth1(CONSUMER_KEY, CONSUMER_SECRET,ACCESS_TOKEN, ACCESS_SECRET)

# Register app - only once!
'''
Mastodon.create_app(
     'sample_mastodon_app',
     scopes=['read', 'write', 'follow'],
     api_base_url = 'https://mastodon.social',
     to_file = 'sample_mastodon_app_clientcred.secret'
)
'''
# Log in - either every time, or use persisted

api = Mastodon(
    client_id = 'b6e91ac10bb933c2e590722d31501510b373ecd4649199f498608caf2eeacb30',
    client_secret = '6165c057a087f2cd461550105760e7c876163ce5a6e55e5c1fa0a215938a7524',
    access_token = 'dc44837f9accdd185fbfee7597e1bba57c5d67c25798349ffec1240b1af8b70e',
    api_base_url = 'https://mastodon.social',
    ratelimit_method = "pace",
    debug_requests=False,
)
api.log_in(
    'f.marsault@protonmail.com',
    'randompasswordFITEC',
)


def tcp():
    TCP_IP = "localhost"
    TCP_PORT = 4444
    conn = None
    s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
    s.bind((TCP_IP, TCP_PORT))
    s.listen(1)
    print("Waiting for TCP connection...")
    conn, addr = s.accept()
    print("Connected... Starting getting tweets.")
    return conn


class StreamUpdate(mastodon.StreamListener):

    def on_update(self, status):
        """A new status has appeared! 'status' is the parsed JSON dictionary
        describing the status."""
        # print(status)
        # tcp_connection = tcp()
        json_toot = status
        try:
            if status['language'] in ['en', 'fr', 'None', 'es']:
                toot_text = str(replace_entities(replace_tags(json_toot['content'])))
                print("Toot Text: " + toot_text)
                print("------------------------------------------")
                message = toot_text + '\n'
                # tcp_connection.send(toot_text + '\n')
        except:
            e = sys.exc_info()[0]
            print("Error: %s" % e)


def get_send_toots():
    # url = 'https://mastodon.social/api/v1/streaming/public'
    listener = StreamUpdate()
    api.stream_public(listener, async=False)


# def send_toots_to_spark(json_toot, tcp_connection):
#     try:
#         toot_text = replace_entities(replace_tags(json_toot['content']))
#         print("Toot Text: " + toot_text)
#         print ("------------------------------------------")
#         # tcp_connection.send(toot_text + '\n')
#     except:
#         e = sys.exc_info()[0]
#         print("Error: %s" % e)

# TCP_IP = "localhost"
# TCP_PORT = 4444
# conn = None
# s = socket.socket(socket.AF_INET, socket.SOCK_STREAM)
# s.bind((TCP_IP, TCP_PORT))
# s.listen(1)
# print("Waiting for TCP connection...")
# conn, addr = s.accept()
# print("Connected... Starting getting tweets.")
json_toot = get_send_toots()
# send_toots_to_spark(json_toot,None)



# This works as intended, which means I manage to connect to my account and use the api
# post = 'try123'
# print("Posting " + post)
# api.toot(post)