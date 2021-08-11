import sys
sys.setrecursionlimit(100000)

import time
import datetime
import dateutil
import config
import uuid
import json
import requests
from os import makedirs
from os.path import join, splitext
from requests.adapters import HTTPAdapter
from requests.packages.urllib3.util.retry import Retry
from retry import retry

from websocket import create_connection
from urllib.parse import urlparse
from gallery_dl import text

from flask import current_app

from ..internals.database.database import get_conn, get_raw_conn, return_conn
from ..lib.artist import index_artists, is_artist_dnp, update_artist, delete_artist_cache_keys, dm_exists, delete_comment_cache_keys, delete_dm_cache_keys
from ..lib.post import post_flagged, post_exists, delete_post_flags, move_to_backup, delete_backup, restore_from_backup, comment_exists
from ..internals.utils.download import download_file, DownloaderException
from ..internals.utils.proxy import get_proxy
from ..internals.utils.logger import log
from ..internals.utils.scrapper import create_scrapper_session

posts_url = 'https://www.patreon.com/api/posts' + '?include=' + ','.join([
    'user',
    'attachments',
    'campaign,poll.choices',
    'poll.current_user_responses.user',
    'poll.current_user_responses.choice',
    'poll.current_user_responses.poll',
    'access_rules.tier.null',
    'images.null',
    'audio.null'
]) + '&fields[post]=' + ','.join([
    'change_visibility_at',
    'comment_count',
    'content',
    'current_user_can_delete',
    'current_user_can_view',
    'current_user_has_liked',
    'embed',
    'image',
    'is_paid',
    'like_count',
    'min_cents_pledged_to_view',
    'post_file',
    'post_metadata',
    'published_at',
    'patron_count',
    'patreon_url',
    'post_type',
    'pledge_url',
    'thumbnail_url',
    'teaser_text',
    'title',
    'upgrade_url',
    'url',
    'was_posted_by_campaign_owner',
    'edited_at'
]) + '&fields[user]=' + ','.join([
    'image_url',
    'full_name',
    'url'
]) + '&fields[campaign]='+ ','.join([
    'show_audio_post_download_links',
    'avatar_photo_url',
    'earnings_visibility',
    'is_nsfw',
    'is_monthly',
    'name',
    'url'
]) + '&fields[access_rule]=' + ','.join([
    'access_rule_type',
    'amount_cents'
]) + '&fields[media]='+ ','.join([
    'id',
    'image_urls',
    'download_url',
    'metadata',
    'file_name',
    'state'
]) + '&sort=-published_at' \
+ '&filter[is_draft]=false' \
+ '&filter[contains_exclusive_posts]=true' \
+ '&json-api-use-default-includes=false&json-api-version=1.0' \
+ '&filter[campaign_id]=' #url should always end with this

campaign_list_url = 'https://www.patreon.com/api/pledges' + '?include=' + ','.join([
    'address',
    'campaign',
    'reward.items',
    'most_recent_pledge_charge_txn',
    'reward.items.reward_item_configuration',
    'reward.items.merch_custom_variants',
    'reward.items.merch_custom_variants.item',
    'reward.items.merch_custom_variants.merch_product_variant'
]) + '&fields[address]=' + ','.join([
    'id',
    'addressee',
    'line_1',
    'line_2',
    'city',
    'state',
    'postal_code',
    'country',
    'phone_number'
]) + '&fields[campaign]=' + ','.join([
    'avatar_photo_url',
    'cover_photo_url',
    'is_monthly',
    'is_non_profit',
    'name',
    'pay_per_name',
    'pledge_url',
    'published_at',
    'url'
]) + '&fields[user]=' + ','.join([
    'thumb_url',
    'url',
    'full_name'
]) + '&fields[pledge]=' + ','.join([
    'amount_cents',
    'currency',
    'pledge_cap_cents',
    'cadence',
    'created_at',
    'has_shipping_address',
    'is_paused',
    'status'
]) + '&fields[reward]=' + ','.join([
    'description',
    'requires_shipping',
    'unpublished_at'
]) + '&fields[reward-item]=' + ','.join([
    'id',
    'title',
    'description',
    'requires_shipping',
    'item_type',
    'is_published',
    'is_ended',
    'ended_at',
    'reward_item_configuration'
]) + '&fields[merch-custom-variant]=' + ','.join([
    'id',
    'item_id'
]) + '&fields[merch-product-variant]=' + ','.join([
    'id',
    'color',
    'size_code'
]) + '&fields[txn]=' + ','.join([
    'succeeded_at',
    'failed_at'
]) + '&json-api-use-default-includes=false&json-api-version=1.0'

bills_url = 'https://www.patreon.com/api/bills' + '?timezone=UTC' + '&include=' + ','.join([
    'post.campaign.null',
    'campaign.null',
    'card.null'
]) + '&fields[campaign]=' + ','.join([
    'avatar_photo_url',
    'currency',
    'cover_photo_url',
    'is_monthly',
    'is_non_profit',
    'is_nsfw',
    'name',
    'pay_per_name',
    'pledge_url',
    'url'
]) + '&fields[post]=' + ','.join([
    'title',
    'is_automated_monthly_charge',
    'published_at',
    'thumbnail',
    'url',
    'pledge_url'
]) + '&fields[bill]=' + ','.join([
    'status',
    'amount_cents',
    'created_at',
    'due_date',
    'vat_charge_amount_cents',
    'vat_country',
    'monthly_payment_basis',
    'patron_fee_cents',
    'is_non_profit',
    'bill_type',
    'currency',
    'cadence',
    'taxable_amount_cents'
]) + '&fields[patronage_purchase]=' + ','.join([
    'amount_cents',
    'currency',
    'created_at',
    'due_date',
    'vat_charge_amount_cents',
    'vat_country',
    'status',
    'cadence',
    'taxable_amount_cents'
]) + '&fields[card]=' + ','.join([ #we fetch the same fields as the patreon site itself to not trigger any possible protections. User card data is actually not saved or accessed.
    'number',
    'expiration_date',
    'card_type',
    'merchant_name',
    'needs_sfw_auth',
    'needs_nsfw_auth'
]) + '&json-api-use-default-includes=false&json-api-version=1.0&filter[due_date_year]='

comments_url = 'https://www.patreon.com/api/posts/{}/comments' + '?include=' + ','.join([
        'commenter.campaign.null',
        'commenter.flairs.campaign',
        'parent',
        'post',
        'first_reply.commenter.campaign.null',
        'first_reply.parent',
        'first_reply.post',
        'exclude_replies',
        'on_behalf_of_campaign.null',
        'first_reply.on_behalf_of_campaign.null'
    ]) + '&fields[comment]=' + ','.join([
        'body',
        'created',
        'deleted_at',
        'is_by_patron',
        'is_by_creator',
        'vote_sum',
        'current_user_vote',
        'reply_count',
    ]) + '&fields[post]=' + ','.join([
        'comment_count'
    ]) + '&fields[user]=' + ','.join([
        'image_url',
        'full_name',
        'url'
    ]) + '&fields[flair]=' + ','.join([
        'image_tiny_url',
        'name' 
    ]) + '&page[count]=10&sort=-created&json-api-use-default-includes=false&json-api-version=1.0'

sendbird_ws_url = 'wss://ws-beaa7a4b-1278-4d71-98fa-a76a9882791e.sendbird.com/' \
+ '?p=JS' \
+ '&sv=3.0.127' \
+ '&ai=BEAA7A4B-1278-4D71-98FA-A76A9882791E' \
+ '&user_id={}' \
+ '&access_token={}' \
+ '&active=1' \
+ '&SB-User-Agent=JS%2Fc3.0.127%2F%2F' \
+ '&Request-Sent-Timestamp={}' \
+ '&include_extra_data=' + ','.join([
    'premium_feature_list',
    'file_upload_size_limit',
    'emoji_hash'
])

sendbird_messages_url = 'https://api-beaa7a4b-1278-4d71-98fa-a76a9882791e.sendbird.com/v3/group_channels/{}/messages' \
+ '?is_sdk=true' \
+ '&prev_limit=15' \
+ '&next_limit=0' \
+ '&include=false' \
+ '&reverse=false' \
+ '&message_ts={}' \
+ '&with_sorted_meta_array=false' \
+ '&include_reactions=false' \
+ '&include_thread_info=false' \
+ '&include_replies=false' \
+ '&include_parent_message_text=false'

sendbird_channels_url = 'https://api-beaa7a4b-1278-4d71-98fa-a76a9882791e.sendbird.com/v3/users/{}/my_group_channels' \
+ '?token={}' \
+ '&limit=15' \
+ '&order=latest_last_message' \
+ '&show_member=true' \
+ '&show_read_receipt=true' \
+ '&show_delivery_receipt=true' \
+ '&show_empty=true' \
+ '&member_state_filter=joined_only' \
+ '&custom_types={}' \
+ '&super_mode=all' \
+ '&public_mode=all' \
+ '&unread_filter=all' \
+ '&hidden_mode=unhidden_only' \
+ '&show_frozen=true'

sendbird_token_url = 'https://www.patreon.com/api/sendbird_session_token?json-api-version=1.0'

current_user_url = 'https://www.patreon.com/api/current_user' \
+ '?include=campaign.null' \
+ '&fields[user]=' + ','.join([
    'full_name',
    'image_url'
]) + '&fields[campaign]=' + ','.join([
    'name',
    'avatar_photo_url'
]) + '&json-api-version=1.0'

members_url = 'https://www.patreon.com/api/members' \
+ '?filter[user_id]={}' \
+ '&filter[can_be_messaged]=true' \
+ '&include=campaign.creator.null' \
+ '&fields[member]=[]' \
+ '&fields[campaign]=' + ','.join([
    'avatar_photo_url',
    'name',
    'url'
]) + '&page[count]=500' + '&json-api-use-default-includes=false&json-api-version=1.0'

current_user_url_with_pledges = 'https://www.patreon.com/api/current_user' \
+ '?include=' + ','.join([
    'pledges.creator.campaign.null',
    'pledges.campaign.null',
    'follows.followed.campaign.null'
]) + '&fields[user]=' + ','.join([
    'image_url',
    'full_name',
    'url',
    'social_connections'
]) + '&fields[campaign]=' + ','.join([
    'avatar_photo_url',
    'creation_name',
    'pay_per_name',
    'is_monthly',
    'is_nsfw',
    'name'
    'url'
]) + '&fields[pledge]=' + ','.join([
    'amount_cents',
    'cadence'
]) + '&fields[follow]=[]' + '&json-api-version=1.0'

@retry(tries=10, delay=2)
def get_ws_connection(url):
    proxy = get_proxy()
    if (proxy):
        proxy_url = urlparse(proxy['https'])
        return create_connection(
            url,
            http_proxy_host=proxy_url.hostname,
            http_proxy_port=proxy_url.port,
            http_proxy_auth=(proxy_url.username, proxy_url.password) if proxy_url.username and proxy_url.password else None,
            proxy_type=proxy_url.scheme
        )
    else:
        return create_connection(url)

#get ids of campaigns with active pledge
def get_active_campaign_ids(key, import_id):
    try:
        scraper = create_scrapper_session().get(campaign_list_url, cookies = { 'session_id': key }, proxies=get_proxy())
        scraper_data = scraper.json()
        scraper.raise_for_status()
    except requests.HTTPError as e:
        log(import_id, f"Status code {e.response.status_code} when contacting Patreon API.", 'exception')
        return set()
    except Exception:
        log(import_id, 'Error connecting to cloudscraper. Please try again.', 'exception')
        return set()

    campaign_ids = set()
    for pledge in scraper_data['data']:
        try:
             campaign_id = pledge['relationships']['campaign']['data']['id']
             campaign_ids.add(campaign_id)
        except Exception as e:
            log(import_id, f"Error while retieving campaign id for pledge {pledge['id']}", 'exception', True)
            continue
    
    return campaign_ids

#Retrieve ids of campaigns for which pledge has been cancelled
#but they've been paid for in this or previous month
def get_cancelled_campaign_ids(key, import_id):
    today_date = datetime.datetime.today()
    bill_data = []
    try:
        scraper = create_scrapper_session().get(bills_url  + str(today_date.year), cookies = { 'session_id': key }, proxies=get_proxy())
        scraper_data = scraper.json()
        scraper.raise_for_status()

        bill_data.extend(scraper_data['data'])

        #get data for previous year as well if today's date is less or equal to january 7th
        if today_date.month == 1 and today_date.day <= 7:
            scraper = create_scrapper_session().get(bills_url  + str(today_date.year - 1), cookies = { 'session_id': key }, proxies=get_proxy())
            scraper_data = scraper.json()
            scraper.raise_for_status()

            if 'data' in scraper_data and len(scraper_data['data']) > 0:
                bill_data.extend(scraper_data['data'])
    except requests.HTTPError as exc:
        log(import_id, f"Status code {exc.response.status_code} when contacting Patreon API.", 'exception')
        return set()
    except Exception:
        log(import_id, 'Error connecting to cloudscraper. Please try again.', 'exception')
        return set()

    bills = []
    for bill in bill_data:
        try:
            if bill['attributes']['status'] != 'successful':
                continue

            due_date = dateutil.parser.parse(bill['attributes']['due_date'])
            
            #We check all bills for the current month as well as bills from the previous month
            #for the first 7 days of the current month because posts are still available 
            #for some time after cancelling membership
            if due_date.month == today_date.month or ((due_date.month == today_date.month - 1 or (due_date.month == 12 and today_date.month == 1)) and today_date.day <= 7):
                bills.append(bill)
        except Exception as e:
            log(import_id, f"Error while parsing one of the bills", 'exception', True)
            continue

    campaign_ids = set()
    
    if len(bills) > 0:
        for bill in bills:
            try:
                campaign_id = bill['relationships']['campaign']['data']['id']
                if not campaign_id in campaign_ids:
                    campaign_ids.add(campaign_id)
            except Exception as e:
                log(import_id, f"Error while retrieving one of the cancelled campaign ids", 'exception', True)
                continue

    return campaign_ids       

def get_campaign_ids(key, import_id):
    active_campaign_ids = get_active_campaign_ids(key, import_id)
    cancelled_campaign_ids = get_cancelled_campaign_ids(key, import_id)

    campaign_ids = set()

    if len(active_campaign_ids) > 0:
        campaign_ids.update(active_campaign_ids)

    if len(cancelled_campaign_ids) > 0:
        campaign_ids.update(cancelled_campaign_ids)

    return list(campaign_ids)

def get_sendbird_token(key, import_id):
    try:
        scraper = create_scrapper_session().get(sendbird_token_url, cookies = { 'session_id': key }, proxies=get_proxy())
        scraper_data = scraper.json()
        scraper.raise_for_status()
    except requests.HTTPError as e:
        log(import_id, f"Status code {e.response.status_code} when contacting Patreon Sendbird token API.", 'exception')
        raise
    except Exception:
        log(import_id, 'Error connecting to cloudscraper. Please try again.', 'exception')
        raise
    
    return scraper_data['session_token']

def get_dm_campaigns(key, current_user_id, import_id):
    try:
        scraper = create_scrapper_session().get(members_url.format(current_user_id), cookies = { 'session_id': key }, proxies=get_proxy())
        campaigns_data = scraper.json()
        scraper.raise_for_status()
    except requests.HTTPError as e:
        log(import_id, f"Status code {e.response.status_code} when contacting Patreon message campaign API.", 'exception')
        raise
    except Exception:
        log(import_id, 'Error connecting to cloudscraper. Please try again.', 'exception')
        raise
    
    return set(campaign['relationships']['campaign']['data']['id'] for campaign in campaigns_data['data'])

def get_current_user_id(key, import_id):
    try:
        scraper = create_scrapper_session().get(current_user_url, cookies = { 'session_id': key }, proxies=get_proxy())
        scraper_data = scraper.json()
        scraper.raise_for_status()
    except requests.HTTPError as e:
        log(import_id, f"Status code {e.response.status_code} when contacting Patreon current user API.", 'exception')
        raise
    except Exception:
        log(import_id, 'Error connecting to cloudscraper. Please try again.', 'exception')
        raise
    
    return scraper_data['data']['id']

def import_channel(auth_token, url, import_id, current_user, contributor_id, timestamp = '9007199254740991'):
    try:
        scraper = create_scrapper_session(useCloudscraper=False).get(sendbird_messages_url.format(url, timestamp), headers = {
            'session-key': auth_token,
            'referer': 'https://www.patreon.com/'
        }, proxies=get_proxy())
        scraper_data = scraper.json()
        scraper.raise_for_status()
    except requests.HTTPError as e:
        log(import_id, f"Status code {e.response.status_code} when contacting DM message API.", 'exception')
        raise

    for message in scraper_data['messages']:
        # https://sendbird.com/docs/chat/v3/platform-api/guides/messages
        dm_id = str(message['message_id'])
        user_id = message['user']['user_id']

        if (message['is_removed']):
            log(import_id, f"Skipping message {dm_id} from user {user_id} because already exists", to_client = False)
            continue

        log(import_id, f"Starting message import: {dm_id} from user {user_id}", to_client = False)

        if (message['type'] == 'MESG'):
            if dm_exists('patreon', user_id, dm_id, message['message']):
                log(import_id, f"Skipping message {dm_id} from user {user_id} because already exists", to_client = False)
                continue
            
            if user_id == current_user:
                log(import_id, f"Skipping message {dm_id} from user {user_id} because it was made by the contributor", to_client = False)
                continue

            if not message['message'].strip():
                log(import_id, f"Skipping message {dm_id} from user {user_id} because it is empty", to_client = False)
                continue

            post_model = {
                'import_id': import_id,
                'contributor_id': contributor_id,
                'id': dm_id,
                '"user"': user_id,
                'service': 'patreon',
                'content': message['message'],
                'embed': {}, # Unused, but could populate with OpenGraph data in the future
                'added': datetime.datetime.now(),
                'published': datetime.datetime.fromtimestamp(message['created_at'] / 1000.0),
                'file': {} # Unused. Might support file DMs if Patreon begins using them.
            }

            post_model['embed'] = json.dumps(post_model['embed'])
            post_model['file'] = json.dumps(post_model['file'])

            columns = post_model.keys()
            data = ['%s'] * len(post_model.values())
            query = "INSERT INTO unapproved_dms ({fields}) VALUES ({values})".format(
                fields = ','.join(columns),
                values = ','.join(data)
            )
            conn = get_raw_conn()
            try:
                cursor = conn.cursor()
                cursor.execute(query, list(post_model.values()))
                conn.commit()
            finally:
                return_conn(conn)
            
            if (config.ban_url):
                requests.request('BAN', f"{config.ban_url}/{post_model['service']}/user/" + user_id + "/dms")
            delete_dm_cache_keys(post_model['service'], user_id)
        elif (message['type'] == 'FILE'):
            log(import_id, f'Skipping message {dm_id} because file DMs are unsupported', to_client=True)
            continue
    
    if (scraper_data['messages']):
        import_channel(auth_token, url, import_id, current_user, contributor_id, timestamp = scraper_data['messages'][0]['created_at'])

def import_channels(auth_token, current_user, campaigns, import_id, contributor_id, token = ''):
    try:
        scraper = create_scrapper_session(useCloudscraper=False).get(sendbird_channels_url.format(current_user, token, ','.join(campaigns)), headers = {
            'session-key': auth_token,
            'referer': 'https://www.patreon.com/'
        }, proxies=get_proxy())
        scraper_data = scraper.json()
        scraper.raise_for_status()
    except requests.HTTPError as e:
        log(import_id, f"Status code {e.response.status_code} when contacting DM channel list API.", 'exception')
        return
    
    for channel in scraper_data['channels']:
        try:
            import_channel(auth_token, channel['channel']['channel_url'], import_id, current_user, contributor_id)
        except Exception as e:
            log(import_id, f"Error while importing DM channel {channel['channel']['channel_url']}", 'exception', True)
            continue

    if (scraper_data['next']):
        import_channels(auth_token, current_user, campaigns, import_id, contributor_id, token = scraper_data['next'])

def import_dms(key, import_id, contributor_id):
    current_user_id = get_current_user_id(key, import_id)
    ws = get_ws_connection(sendbird_ws_url.format(current_user_id, get_sendbird_token(key, import_id), round(time.time() * 1000)))
    ws_data = json.loads(ws.recv().replace('LOGI', ''))
    ws.close()

    import_channels(ws_data['key'], current_user_id, get_dm_campaigns(key, current_user_id, import_id), import_id, contributor_id)

def import_comment(comment, user_id, import_id):
    post_id = comment['relationships']['post']['data']['id']
    commenter_id = comment['relationships']['commenter']['data']['id']
    comment_id = comment['id']
    
    if comment_exists('patreon', commenter_id, comment_id):
        log(import_id, f"Skipping comment {comment_id} from post {post_id} because already exists", to_client = False)
        return

    if (comment['attributes']['deleted_at']):
        log(import_id, f"Skipping comment {comment_id} from post {post_id} because it is deleted", to_client = False)
        return
    
    log(import_id, f"Starting comment import: {comment_id} from post {post_id}", to_client = False)

    post_model = {
        'id': comment_id,
        'post_id': post_id,
        'parent_id': comment['relationships']['parent']['data']['id'] if comment['relationships']['parent']['data'] else None,
        'commenter': commenter_id,
        'service': 'patreon',
        'content': comment['attributes']['body'],
        'added': datetime.datetime.now(),
        'published': comment['attributes']['created'],
    }

    columns = post_model.keys()
    data = ['%s'] * len(post_model.values())
    query = "INSERT INTO comments ({fields}) VALUES ({values})".format(
        fields = ','.join(columns),
        values = ','.join(data)
    )
    conn = get_raw_conn()
    try:
        cursor = conn.cursor()
        cursor.execute(query, list(post_model.values()))
        conn.commit()
    finally:
        return_conn(conn)

    if (config.ban_url):
        requests.request('BAN', f"{config.ban_url}/{post_model['service']}/user/" + user_id + '/post/' + post_model['post_id'])
    delete_comment_cache_keys(post_model['service'], user_id, post_model['post_id'])

def import_comments(url, key, post_id, user_id, import_id):
    try:
        scraper = create_scrapper_session().get(url, cookies = { 'session_id': key }, proxies=get_proxy())
        scraper_data = scraper.json()
        scraper.raise_for_status()
    except requests.HTTPError as e:
        log(import_id, f"Status code {e.response.status_code} when contacting Patreon API.", 'exception')
        return
    except Exception:
        log(import_id, 'Error connecting to cloudscraper. Please try again.', 'exception')
        return
    
    for comment in scraper_data['data']:
        comment_id = comment['id']
        try:
            import_comment(comment, user_id, import_id)
        except Exception as e:
            log(import_id, f"Error while importing comment {comment_id} from post {post_id}", 'exception', True)
            continue
    
    if scraper_data.get('included'):
        for included in scraper_data['included']:
            if (included['type'] == 'comment'):
                comment_id = comment['id']
                try:
                    import_comment(included, user_id, import_id)
                except Exception as e:
                    log(import_id, f"Error while importing comment {comment_id} from post {post_id}", 'exception', True)
                    continue
    
    if 'links' in scraper_data and 'next' in scraper_data['links']:
        log(import_id, f"Processing next page of comments for post {post_id}", to_client = False)
        import_comments(scraper_data['links']['next'], key, post_id, user_id, import_id)

def import_campaign_page(url, key, import_id): 
    try:
        scraper = create_scrapper_session().get(url, cookies = { 'session_id': key }, proxies=get_proxy())
        scraper_data = scraper.json()
        scraper.raise_for_status()
    except requests.HTTPError as e:
        log(import_id, f"Status code {e.response.status_code} when contacting Patreon API.", 'exception')
        return
    except Exception:
        log(import_id, 'Error connecting to cloudscraper. Please try again.', 'exception')
        return
    
    user_id = None
    for post in scraper_data['data']:
        backup_path = None
        try:
            user_id = post['relationships']['user']['data']['id']
            post_id = post['id']
            file_directory = f"files/{user_id}/{post_id}"
            attachments_directory = f"attachments/{user_id}/{post_id}"

            if is_artist_dnp('patreon', user_id):
                log(import_id, f"Skipping user {user_id} because they are in do not post list", to_client = True)
                return

            if not post['attributes']['current_user_can_view']:
                log(import_id, f'Skipping {post_id} from user {user_id} because this post is not available for current subscription tier', to_client = True)
                continue            

            import_comments(comments_url.format(post_id), key, post_id, user_id, import_id)

            if post_exists('patreon', user_id, post_id) and not post_flagged('patreon', user_id, post_id):
                log(import_id, f'Skipping post {post_id} from user {user_id} because already exists', to_client = True)
                continue

            if post_flagged('patreon', user_id, post_id):
                backup_path = move_to_backup('patreon', user_id, post_id)

            log(import_id, f"Starting import: {post_id} from user {user_id}")

            post_model = {
                'id': post_id,
                '"user"': user_id,
                'service': 'patreon',
                'title': post['attributes']['title'] or "",
                'content': '',
                'embed': {},
                'shared_file': False,
                'added': datetime.datetime.now(),
                'published': post['attributes']['published_at'],
                'edited': post['attributes']['edited_at'],
                'file': {},
                'attachments': []
            }

            if post['attributes']['content']:
                post_model['content'] = post['attributes']['content']
                for image in text.extract_iter(post['attributes']['content'], '<img data-media-id="', '>'):
                    download_url = text.extract(image, 'src="', '"')[0]
                    path = urlparse(download_url).path
                    ext = splitext(path)[1]
                    fn = str(uuid.uuid4()) + ext
                    filename, _ = download_file(
                        join(config.download_path, 'inline'),
                        download_url,
                        name = fn
                    )
                    post_model['content'] = post_model['content'].replace(download_url, f"/inline/{filename}")

            if post['attributes']['embed']:
                post_model['embed']['subject'] = post['attributes']['embed']['subject']
                post_model['embed']['description'] = post['attributes']['embed']['description']
                post_model['embed']['url'] = post['attributes']['embed']['url']

            if post['attributes']['post_file']:
                filename, _ = download_file(
                    join(config.download_path, file_directory),
                    post['attributes']['post_file']['url'],
                    name = post['attributes']['post_file']['name']
                )
                post_model['file']['name'] = post['attributes']['post_file']['name']
                post_model['file']['path'] = f'/{file_directory}/{filename}'

            for attachment in post['relationships']['attachments']['data']:
                filename, _ = download_file(
                    join(config.download_path, attachments_directory),
                    f"https://www.patreon.com/file?h={post_id}&i={attachment['id']}",
                    cookies = { 'session_id': key }
                )
                post_model['attachments'].append({
                    'name': filename,
                    'path': f'/{attachments_directory}/{filename}'
                })

            if post['relationships']['images']['data']:
                for image in post['relationships']['images']['data']:
                    for media in list(filter(lambda included: included['id'] == image['id'], scraper_data['included'])):
                        if media['attributes']['state'] != 'ready':
                            continue
                        filename, _ = download_file(
                            join(config.download_path, attachments_directory),
                            media['attributes']['download_url'],
                            name = media['attributes']['file_name']
                        )
                        post_model['attachments'].append({
                            'name': filename,
                            'path': f'/{attachments_directory}/{filename}'
                        })

            if post['relationships']['audio']['data']:
                for media in list(filter(lambda included: included['id'] == post['relationships']['audio']['data']['id'], scraper_data['included'])):
                    if media['attributes']['state'] != 'ready':
                        continue
                    filename, _ = download_file(
                        join(config.download_path, attachments_directory),
                        media['attributes']['download_url'],
                        name = media['attributes']['file_name']
                    )
                    post_model['attachments'].append({
                        'name': filename,
                        'path': f'/{attachments_directory}/{filename}'
                    })

            post_model['embed'] = json.dumps(post_model['embed'])
            post_model['file'] = json.dumps(post_model['file'])
            for i in range(len(post_model['attachments'])):
                post_model['attachments'][i] = json.dumps(post_model['attachments'][i])

            columns = post_model.keys()
            data = ['%s'] * len(post_model.values())
            data[-1] = '%s::jsonb[]' # attachments
            query = "INSERT INTO posts ({fields}) VALUES ({values}) ON CONFLICT (id, service) DO UPDATE SET {updates}".format(
                fields = ','.join(columns),
                values = ','.join(data),
                updates = ','.join([f'{column}=EXCLUDED.{column}' for column in columns])
            )
            conn = get_raw_conn()
            try:
                cursor = conn.cursor()
                cursor.execute(query, list(post_model.values()))
                conn.commit()
            finally:
                return_conn(conn)

            update_artist('patreon', user_id)
            delete_post_flags('patreon', user_id, post_id)
            
            if (config.ban_url):
                requests.request('BAN', f"{config.ban_url}/{post_model['service']}/user/" + post_model['"user"'])
            delete_artist_cache_keys('patreon', user_id)

            if backup_path is not None:
                delete_backup(backup_path)
            log(import_id, f"Finished importing {post_id} from user {user_id}", to_client=False)
        except Exception as e:
            log(import_id, f"Error while importing {post_id} from user {user_id}", 'exception', True)
            if backup_path is not None:
                restore_from_backup('patreon', user_id, post_id, backup_path)
            continue
    
    if 'links' in scraper_data and 'next' in scraper_data['links']:
        log(import_id, f'Finished processing page. Processing next page.')
        import_campaign_page(scraper_data['links']['next'], key, import_id)
    else:
        log(import_id, f"Finished scanning for posts.")
        index_artists()

def import_posts(import_id, key, allowed_to_scrape_dms, contributor_id):
    if (allowed_to_scrape_dms):
        log(import_id, f"Importing DMs...", to_client = True)
        import_dms(key, import_id, contributor_id)
        log(import_id, f"Done importing DMs.", to_client = True)
    campaign_ids = get_campaign_ids(key, import_id)
    if len(campaign_ids) > 0:
        for campaign_id in campaign_ids:
            log(import_id, f"Importing campaign {campaign_id}", to_client = True)
            import_campaign_page(posts_url + str(campaign_id), key, import_id)
    else:
        log(import_id, f"No active subscriptions or invalid key. No posts will be imported.", to_client = True)

if __name__ == '__main__':
    if len(sys.argv) > 1:
        import_posts(str(uuid.uuid4()), sys.argv[1])
    else:
        print('Argument required - Login token')
