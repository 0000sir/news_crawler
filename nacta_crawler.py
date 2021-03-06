#! /usr/bin/env python
# encoding=utf-8
import requests
from urlparse import urljoin
import codecs
from bs4 import BeautifulSoup
import pymongo
import sys
import os
import argparse
import time, threading

ROOT_URL = "https://www.nacta.edu.cn/xwgg/xyxw/index.htm"
MAX_THREADS = 32

# create db connection
mongo = pymongo.MongoClient('mongodb://root:averystrongandstupidpassword@localhost:27017')
db = mongo['nacta_news']

lock = threading.Lock()

def read_page(url):
    data = requests.get(url).content
    return data

def index_urls():
    urls = []
    base = "https://www.nacta.edu.cn/xwgg/xyxw/"
    for i in range(447): # 447 totals
        if i==0:
            url = urljoin(base, 'index.htm')
        else:
            url = urljoin(base, 'index%d.htm'%i)
        urls.append(url)
    return urls

def parse_index(url):
    print("Reading links from index %s" % url)
    html = read_page(url)
    soup = BeautifulSoup(html, features="html5lib")
    news_list_soup = soup.find('div', attrs={'class':'view-campus-news'})
    urls = []
    for news_link in news_list_soup.find_all('a', attrs={'target': '_blank'}):
        raw_url = news_link.get('href')
        news_url = urljoin(url, raw_url)
        id = int(news_url[news_url.rfind('/')+1:news_url.rfind('.')])
        news_title = news_link.getText()
        urls.append({'_id': id, 'url': news_url})
    return urls

def parse_news(url):
    print('reading html from %s' % url)
    html = read_page(url)
    soup = BeautifulSoup(html, features="html5lib")
    title = soup.find('h1', attrs={'class': 'title'})
    content = soup.find('div', attrs={'class': 'node'}).find('div', attrs={'class': 'content'})
    text = content.getText()
    images = content.find_all('img')
    img_urls = []
    for img in images:
        img_url = urljoin(url, img.get('src'))
        img_urls.append(img_url)
    return {'title': title.getText(), 'body': text, 'image_urls': img_urls}

def find_original_image(url):
    last_slash = url.rfind('/')
    filename = url[last_slash+1:]
    original_filename = filename[filename.find('_')+1:]
    return url[0:last_slash+1]+original_filename

def download_images(image_urls):
    for url in image_urls:
        download_image(url)
        download_image(find_original_image(url))

def download_image(image_url):
    print("Downloading image %s" % image_url)
    filename = image_url[image_url.rfind('/')+1:]
    dir_start = image_url.find('content')+8
    dir_end = image_url.rfind('/')
    directory = image_url[dir_start:dir_end]
    directory = "images/%s" % directory
    if not os.path.exists(directory):
        os.makedirs(directory)
    path = "%s/%s" % (directory, filename)
    r = requests.get(image_url, stream=True)
    if r.status_code == 200:
        with open(path, 'wb') as f:
            for chunk in r.iter_content(1024):
                f.write(chunk)


def save_news_url(urls):
    db['urls'].insert_many(urls)

def fetch_batch_virgin(count):
    lock.acquire()
    urls = []
    try:
        virgin = list(db['urls'].find({'state': {'$exists': False}}).limit(count))
        for i in range(len(virgin)):
            virgin[i]['state'] = 'load_html'
            db['urls'].update_one(filter={'_id': virgin[i]['_id']}, update={'$set': virgin[i]})
        urls = virgin
    finally:
        lock.release()
    return urls

def fetch_batch_pregnant(count):
    lock.acquire()
    urls = []
    try:
        virgin = list(db['urls'].find({'state': 'html_loaded'}).limit(count))
        for i in range(len(virgin)):
            virgin[i]['state'] = 'load_image'
            db['urls'].update_one(filter={'_id': virgin[i]['_id']}, update={'$set': virgin[i]})
        urls = virgin
    finally:
        lock.release()
    return urls

def content_thread():
    print("Getting html content in thread %s" % threading.current_thread().name)
    while db['urls'].count_documents({'state': {'$exists': False}})>0 :
        urls = fetch_batch_virgin(10)
        for url in urls:
            content = parse_news(url['url'])
            url['state'] = 'html_loaded'
            content = dict(url, **content)
            db['urls'].update_one(filter={'_id': url['_id']}, update={'$set': content})
    print("Exiting html content thread %s" % threading.current_thread().name)

def image_thread():
    print("Downloading images in thread %s" % threading.current_thread().name)
    while db['urls'].count_documents({'state': 'html_loaded'})>0:
        urls = fetch_batch_pregnant(10)
        for url in urls:
            download_images(url['image_urls'])
            url['state'] = 'images_loaded'
            db['urls'].update_one(filter={'_id': url['_id']}, update={'$set': url})
    print("Exiting image downloading thread %s" % threading.current_thread().name)

def main(args):
    if (args.mode=='urls'):
        print('Getting news urls')
        db['urls'].remove({})
        for page in index_urls():
            news_urls = parse_index(page)
            print('Saving %d urls' % len(news_urls))
            save_news_url(news_urls)
    elif(args.mode=='contents'):
        print('Getting news contents')
        for i in range(MAX_THREADS):
            t = threading.Thread(target=content_thread, name='Content Thread %d' % i)
            t.start()
    elif(args.mode=='images'):
        print('Downloading images')
        for i in range(MAX_THREADS):
            t = threading.Thread(target=image_thread, name='Image Thread %d' % i)
            t.start()
    elif(args.mode=='clear_db'):
        print('Clearing db contents')
        db['urls'].update_many({'state': {'$exists': True}}, {'$unset': {"state": True}})
    elif(args.mode=='clear_img'):
        print('Clearing db image status')
        db['urls'].update_many({}, {'$set': {'state': 'html_loaded'}})

def parse_arguments(argv):
    parser = argparse.ArgumentParser()
    parser.add_argument('mode', type=str, choices=['urls', 'contents', 'images', 'clear_db', 'clear_img'])

    return parser.parse_args(argv)

if __name__=='__main__':
    main(parse_arguments(sys.argv[1:]))
