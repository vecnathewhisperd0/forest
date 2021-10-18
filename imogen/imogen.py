#!/usr/bin/python3.9
import asyncio
import json
import time
import logging
import os
import base64
from typing import Optional
from pathlib import Path
import aioredis
import aiohttp
from aiohttp import web
from forest import utils
from forest.core import Bot, Message, app

if not utils.LOCAL:
    aws_cred = utils.get_secret("AWS_CREDENTIALS")
    if aws_cred:
        aws_dir = Path("/root/.aws")
        aws_dir.mkdir(parents=True, exist_ok=True)
        with (aws_dir / "credentials").open("w") as creds:
            creds.write(base64.b64decode(utils.get_secret("AWS_CREDENTIALS")).decode())
        logging.info("wrote creds")
        with (aws_dir / "config").open("w") as config:
            config.write("[profile default]\nregion = us-east-1")
        logging.info("writing config")
    else:
        logging.info("couldn't find creds")
    ssh_key = utils.get_secret("SSH_KEY")
    open("id_rsa", "w").write(base64.b64decode(ssh_key).decode())
url = (
    utils.get_secret("FLY_REDIS_CACHE_URL")
    or "redis://:***REMOVED***@***REMOVED***:10079"
)
password, rest = url.lstrip("redis://:").split("@")
host, port = rest.split(":")
redis = aioredis.Redis(host=host, port=int(port), password=password)

instance_id = "aws ec2 describe-instances --region us-east-1 | jq -r .Reservations[].Instances[].InstanceId"
status = "aws ec2 describe-instances --region us-east-1| jq -r '..|.State?|.Name?|select(.!=null)'"
start = "aws ec2 start-instances --region us-east-1 --instance-ids {}"
stop = "aws ec2 stop-instances --region us-east-1 --instance-ids {}"
get_ip = "aws ec2 describe-instances --region us-east-1|jq -r .Reservations[].Instances[].PublicIpAddress"
start_worker = "ssh -i id_rsa -o ConnectTimeout=2 ubuntu@{} ~/ml/read_redis.py {}"


async def get_output(cmd: str) -> str:
    proc = await asyncio.create_subprocess_shell(cmd, stdout=-1)
    stdout, _ = await proc.communicate()
    return stdout.decode().strip()


async def really_start_worker() -> None:
    ip = await get_output(get_ip)
    while 1:
        await asyncio.create_subprocess_shell(
            start_worker.format(ip, url), stdout=-1, stderr=-1
        )
        # don't *block* since output runs forever, but check if failed...


class Imogen(Bot):
    worker_instance_id: Optional[str] = None

    async def start_process(self) -> None:
        self.worker_instance_id = await get_output(instance_id)
        await super().start_process()

    async def set_profile(self) -> None:
        profile = {
            "command": "updateProfile",
            "given-name": "imogen",
            "about": "imagine there's an imoge generated",
            "about-emoji": "\N{Artist Palette}",
            "family-name": "",
        }
        await self.signalcli_input_queue.put(profile)
        os.symlink(".", "state")
        logging.info(profile)

    async def do_status(self, _: Message) -> str:
        state = await get_output(status)
        queue_size = await redis.llen("prompt_queue")
        return f"worker state: {state}, queue size: {queue_size}"

    async def do_imagine(self, msg: Message) -> str:
        logging.info(msg.full_text)
        logging.info(msg.text)
        await redis.rpush(
            "prompt_queue",
            json.dumps({"prompt": msg.text, "callback": msg.group or msg.source}),
        )
        # check if worker is up
        state = await get_output(status)
        if state == "stopped":
            # if not, turn it on
            logging.info(await get_output(start.format(self.worker_instance_id)))
            asyncio.create_task(really_start_worker())
        timed = await redis.llen("prompt_queue")
        return f"you are #{timed} in line"

    async def do_stop(self, _: Message) -> str:
        return await get_output(stop.format(self.worker_instance_id))

    # eh
    # async def async_shutdown(self):
    #    await redis.disconnect()
    #    super().async_shutdown()


async def admin_handler(request: web.Request) -> web.Response:
    bot = request.app.get("bot")
    if not bot:
        return web.Response(status=504, text="Sorry, no live workers.")
    await bot.send_message(utils.get_secret("ADMIN"), request.query.get("message"))
    return web.Response(text="OK")


async def store_image_handler(request: web.Request) -> web.Response:
    bot = request.app.get("bot")
    if not bot:
        return web.Response(status=504, text="Sorry, no live workers.")
    reader = await request.multipart()
    # /!\ Don't forget to validate your inputs /!\
    # reader.next() will `yield` the fields of your form
    field = await reader.next()
    if not isinstance(field, aiohttp.BodyPartReader):
        return web.Response(text="bad form")
    print(field.name)
    # assert field.name == "image"
    filename = field.filename or f"attachment-{time.time()}"
    # You cannot rely on Content-Length if transfer is chunked.
    size = 0
    path = Path(filename).absolute()
    with open(path, "wb") as f:
        while True:
            chunk = await field.read_chunk()  # 8192 bytes by default.
            if not chunk:
                break
            size += len(chunk)
            f.write(chunk)
    destination = request.query.get("destination", "")
    recipient = utils.signal_format(destination)
    group = None if recipient else destination
    message = request.query.get("message", "")
    if recipient:
        await bot.send_message(recipient, message, attachments=[str(path)])
    else:
        await bot.send_message(None, message, attachments=[str(path)], group=group)
    return web.Response(text="{} sized of {} sent" "".format(filename, size))


app.add_routes([web.post("/attachment/{phonenumber}", store_image_handler)])
app.add_routes([web.post("/admin", admin_handler)])


if __name__ == "__main__":

    @app.on_startup.append
    async def start_wrapper(out_app: web.Application) -> None:
        out_app["bot"] = Imogen()

    web.run_app(app, port=8080, host="0.0.0.0")