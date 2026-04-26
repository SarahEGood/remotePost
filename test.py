from atproto import Client, client_utils
import sqlite3
import time
import json
from datetime import datetime, timezone
from zoneinfo import ZoneInfo
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

def create_post(text, image_paths=None, alt_text=[""]):
    images = []

    if image_paths:
        for i in range(len(image_paths)):
            images.append(Image(path=image_paths[i],
                                alt_text=alt_text[i]))

    return Post(text=text,\
                images=images
                )

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
                print(post)
                
                if platform == "bluesky":
                    print('post to bsky')
                    postToBluesky(creds, post)

                cur.execute(
                    "UPDATE scheduled_posts SET status = 'done' WHERE id= ?",
                    (job_id,)
                )

            except Exception as e:
                import traceback
                cur.execute(
                    "UPDATE scheduled_posts SET status = 'failed' WHERE id = ?",
                    (job_id,)
                )
                print("Or here?")
                traceback.print_exc()

        conn.commit()
        time.sleep(10)

def getCredentials():
    with open('credentials.txt') as file:
        creds = [line.rstrip() for line in file]
    return creds

def uploadImages(client, images):
    uploaded = []

    for img in images:
        print(img)
        with open(img.path, "rb") as f:
            blob = client.upload_blob(f.read())
            uploaded.append({
                "$type": "app.bsky.embed.images#image",
                "image": blob["blob"],
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
        uploaded_images = []

        for img in post.images:
            with open(img.path, "rb") as f:
                blob = client.upload_blob(f.read())

                uploaded_images.append({
                    "$type": "app.bsky.embed.images#image",
                    "image": blob["blob"],  # <-- CRITICAL
                    "alt": img.alt_text or ""
                })

        embed = {
            "$type": "app.bsky.embed.images",
            "images": uploaded_images
        }

    try:
        client.send_post(
            text=post.text,
            embed=embed
        )
    except Exception:
        import traceback
        traceback.print_exc()

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

    # Year, Month, Day, Hour, Minute, Second
    local_time = datetime(2026, 4, 26, 8, tzinfo=ZoneInfo("America/Los_Angeles"))
    print(local_time)
    run_at = local_time.astimezone(ZoneInfo("Etc/UTC"))
    print(run_at)

    post = create_post(
        "Jojos but like, what if horse girl #Jjba #umamusume",
        ['images/uma_kakyoin01.png'],
        alt_text=['']
    )

    creds = getCredentials()
    schedulePost(post, 'bluesky', run_at)
    workerLoop(creds)