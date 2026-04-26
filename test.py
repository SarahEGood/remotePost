from atproto import Client, client_utils
import sqlite3
import time
import json
from datetime import datetime, timezone
from dataclasses import dataclass, asdict, field
from typing import List, Optional

@dataclass
class Image:
    path: str
    alt_text: Optional[str] = None

@dataclass
class Post:
    text: str
    images: List[Image] = field(default_factory=list)
    scheduled_time: Optional[datetime] = None
    platforms: List[str] = None

def create_post(text, image_paths=None):
    images = []

    if image_paths:
        for path in image_paths:
            images.append(Image(path=path))

    return Post(text=text, images=images)

def serialize_post(post):
    return json.dumps(asdict(post))

def deserialize_post(post_json):
    data = json.loads(post_json)

    images = [
        Image(**img) for img in (data.get("images") or [])
    ]

    return Post(
        text=data["text"],
        images=images
    )

def schedulePost(post: Post, platform, run_at):
    cur.execute(
        "INSERT INTO scheduled_posts (platform, post_json, run_at, status) VALUES (?, ?, ?, ?)",
        (platform, serialize_post(post), run_at, "pending")
    )

def workerLoop(creds):
    while True:
        now = datetime.now(timezone.utc)

        cur.execute("""
            SELECT id, platform, post_json FROM scheduled_posts
            WHERE status = 'pending' AND run_at <= ?
        """, (now,))

        jobs = cur.fetchall()

        for job_id, platform, post_json in jobs:
            try:
                post = deserialize_post(post_json)
                
                if platform == "bluesky":
                    postToBluesky(creds, post)

                cur.execute(
                    "UPDATE scheduled_posts SET status = 'done' WHERE id= ?",
                    (job_id,)
                )

            except Exception as e:
                cur.execute(
                    "UPDATE scheduled_posts SET status = 'failed' WHERE id = ?",
                    (job_id,)
                )
                print("Or here?")

        conn.commit()
        time.sleep(10)

def getCredentials():
    with open('credentials.txt') as file:
        creds = [line.rstrip() for line in file]
    return creds

def uploadImages(images):
    uploaded = []

    for img in images:
        with open(img.path, "rb") as f:
            blob = client.upload_blob(f.read())
            uploaded.append({
                "image": blob,
                "alt": img.alt_text or ""
            })

    return uploaded

def buildFacets(text):
    facets = []

    import re
    for match in re.finditer(r"https?://\S+", text):
        facets.append({
            "index": {
                "byteStart": match.start(),
                "byteEnd": match.end()
            },
            "features": [{
                "$type": "app.bsky.richtext.facet#link",
                "uri": match.group()
            }]
        })
    
    return facets

def postToBluesky(creds, post: Post):
    handle = creds[0]
    password = creds[1]

    client = Client()
    client.login(handle, password)

    embed = None

    if post.images:
        uploaded_images = uploadImages(post.images)

        embed = {
            "$type": "app.bsky.embed.images",
            "images": uploaded_images
        }

    client.send_post(
        text=post.text,
        embed=embed
    )

def richText(creds) -> None:
    client = Client()
    client.login(creds[0],creds[1])

    client.send_post(client_utils.TextBuilder().text('Hey everyone I made a ').link('Pixiv', 'https://www.pixiv.net/en/users/123184015').text('.'))


if __name__ == '__main__':
    conn = sqlite3.connect('scheduler.db')
    cur = conn.cursor()

    cur.execute("""
    CREATE TABLE IF NOT EXISTS scheduled_posts (
                id INTEGER PRIMARY KEY,
                platform TEXT,
                post_json TEXT,
                run_at DATETIME,
                status TEXT)
    """)


    run_at = datetime.strptime("26/04/26 6:00", "%d/%m/%y %H:%M")
    run_at = datetime.now(timezone.utc) #temp for testing

    post = create_post(
        "Hello",
        ['img1.jpg', 'img2.jpg']
    )

    creds = getCredentials()
    schedulePost(post, 'bluesky', run_at)
    workerLoop(creds)