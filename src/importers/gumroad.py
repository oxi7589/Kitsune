import sys
sys.setrecursionlimit(100000)

import re
import config
import requests
import uuid
import json
import datetime
from bs4 import BeautifulSoup
from os.path import join
from os import makedirs

from flask import current_app

from ..internals.database.database import get_conn, get_raw_conn, return_conn
from ..lib.artist import index_artists, is_artist_dnp, update_artist, delete_artist_cache_keys
from ..lib.post import post_flagged, post_exists, delete_post_flags, move_to_backup, delete_backup, restore_from_backup
from ..internals.utils.download import download_file, DownloaderException
from ..internals.utils.proxy import get_proxy
from ..internals.utils.logger import log
from ..internals.utils.utils import get_value
from ..internals.utils.scrapper import create_scrapper_session

def import_posts_from_user(import_id, key, user_info_list, offset = 1):
    try:
        user_id = user_info_list[2]
        scraper = create_scrapper_session().get(
            f"https://gumroad.com/discover_search?from={offset}&user_purchases_only=true&creator_external_ids%5B%5D={user_id}",
            cookies = { '_gumroad_app_session': key },
            proxies=get_proxy()
        )
        scraper_data = scraper.json()
        scraper.raise_for_status()
    except requests.HTTPError:
        log(import_id, f'Status code {scraper_data.status_code} when contacting Gumroad API.', 'exception')
        return

    if (scraper_data['total'] > 100000):
        log(import_id, f"Can't log in; is your session key correct?")
        return
    
    soup = BeautifulSoup(scraper_data['products_html'], 'html.parser')
    products = soup.find_all(class_='product-card')

    for product in products:
        try:
            backup_path = None

            post_id = product['data-permalink']
            cover_url = None
            purchase_download_url = None

            properties_element = product.find('div', {'data-react-class':'Product/LibraryCard'})
            react_props = json.loads(properties_element['data-react-props'])
            if not 'purchase' in react_props:
                log(import_id, f"Skipping post {post_id} from user {user_id} because it has no purchase data")
                continue
            elif react_props['purchase']['is_archived']:
                # this check is redundant, but better safe than sorry:
                # archived products may contain sensitive data such as a watermark with an e-mail on it
                log(import_id, f"Skipping post {post_id} from user {user_id} because it is archived")
                continue

            purchase_download_url = react_props['purchase']['download_url']

            react_props_product = react_props['product']
            title = react_props_product['name']

            # sanity check to make sure creator_external_ids[] url filter has done its job
            # (except for creators who have closed their stores, there is no way to double-check those)
            if 'creator' in react_props_product and react_props_product['creator']:
                creator_name = react_props_product['creator']['name']
                creator_name_expected = user_info_list[1]
                if creator_name_expected.strip() != creator_name.strip():
                    log(import_id, f"Skipping post {post_id} from user {user_id} because of inconsistent creator data")
                    continue

            file_directory = f"files/gumroad/{user_id}/{post_id}"
            attachments_directory = f"attachments/gumroad/{user_id}/{post_id}"

            if is_artist_dnp('gumroad', user_id):
                log(import_id, f"Skipping post {post_id} from user {user_id} is in do not post list")
                continue

            if post_exists('gumroad', user_id, post_id) and not post_flagged('gumroad', user_id, post_id):
                log(import_id, f'Skipping post {post_id} from user {user_id} because already exists')
                continue

            if post_flagged('gumroad', user_id, post_id):
                backup_path = move_to_backup('gumroad', user_id, post_id)

            log(import_id, f"Starting import: {post_id} from user {user_id}")

            post_model = {
                'id': post_id,
                '"user"': user_id,
                'service': 'gumroad',
                'title': title,
                'content': '',
                'embed': {},
                'shared_file': False,
                'added': datetime.datetime.now(),
                'published': None,
                'edited': None,
                'file': {},
                'attachments': []
            }

            if 'main_cover_id' in react_props_product:
                main_cover_id = react_props_product['main_cover_id']
                for cover in react_props_product['covers']:
                    if cover['id'] == main_cover_id:
                        cover_url = get_value(cover, 'original_url') or cover['url']

            scraper3 = create_scrapper_session().get(
                purchase_download_url,
                cookies = { '_gumroad_app_session': key },
                proxies=get_proxy()
            )
            scraper_data3 = scraper3.text
            soup3 = BeautifulSoup(scraper_data3, 'html.parser')

            try:
                download_data = json.loads(soup3.select_one('div[data-react-class="DownloadPage/FileList"]')['data-react-props'])
            except:
                download_data = {
                  "content_items": []
                }

            if cover_url:
                filename, _ = download_file(
                    join(config.download_path, file_directory),
                    cover_url
                )
                post_model['file']['name'] = filename
                post_model['file']['path'] = f'/{file_directory}/{filename}'

            for _file in download_data['content_items']:
                if (_file['type'] == 'file'):
                    filename, _ = download_file(
                        join(config.download_path, attachments_directory),
                        'https://gumroad.com' + _file['download_url'],
                        name = f'{_file["file_name"]}.{_file["extension"].lower()}',
                        cookies = { '_gumroad_app_session': key }
                    )
                    post_model['attachments'].append({
                        'name': filename,
                        'path': f'/{attachments_directory}/{filename}'
                    })
                else:
                    log(import_id, f"Unsupported content found in product {post_id}. You should tell Shino about this.", to_client=True)
                    log(import_id, json.dumps(_file), to_client=False)
                    continue

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

            update_artist('gumroad', user_id)
            delete_post_flags('gumroad', user_id, post_id)

            if (config.ban_url):
                requests.request('BAN', f"{config.ban_url}/{post_model['service']}/user/" + post_model['"user"'])
            delete_artist_cache_keys('gumroad', user_id)
            
            if backup_path is not None:
                delete_backup(backup_path)
            log(import_id, f"Finished importing post {post_id} from user {user_id}", to_client = False)
        except Exception as e:
            log(import_id, f"Error while importing {post_id} from user {user_id}", 'exception')
            if backup_path is not None:
                restore_from_backup('gumroad', user_id, post_id, backup_path)
            continue

    if len(products):
        next_offset = offset + scraper_data['result_count']
        log(import_id, f'Finished processing offset {offset}. Processing offset {next_offset}')
        import_posts_from_user(import_id, key, user_info_list, offset=next_offset)


def import_posts(import_id, key):
    try:
        scraper = create_scrapper_session().get(
            f"https://gumroad.com/discover_search?user_purchases_only=true",
            cookies = { '_gumroad_app_session': key },
            proxies=get_proxy()
        )
        scraper_data = scraper.json()
        scraper.raise_for_status()
    except requests.HTTPError:
        log(import_id, f'Status code {scraper_data.status_code} when contacting Gumroad API.', 'exception')
        return

    if (scraper_data['total'] > 100000):
        log(import_id, f"Can't log in; is your session key correct?")
        return

    user_displayname_by_id = {}
    try:
        for user_info_list in scraper_data['creator_counts'].keys():
            parsed_user_info_list = json.loads(user_info_list) # (username, display name, ID), username can be null
            user_id = parsed_user_info_list[2]
            user_displayname_by_id[user_id] = parsed_user_info_list[1]
            log(import_id, f"Importing posts from user {user_id}")
            import_posts_from_user(import_id, key, parsed_user_info_list)
    finally:
        log(import_id, f"Finished scanning for posts.")
        index_artists(gumroad_name_fallback=user_displayname_by_id)

if __name__ == '__main__':
    if len(sys.argv) > 1:
        import_posts(str(uuid.uuid4()), sys.argv[1])
    else:
        print('Argument required - Login token')
