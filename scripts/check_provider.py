"""Opt-in provider preflight. --images makes two paid image endpoint calls.
Never prints provider response bodies or credentials.
"""
import argparse
import base64
import io
import sys
from pathlib import Path
sys.path.insert(0,str(Path(__file__).resolve().parents[1]))
from app import config
from openai import OpenAI, APIStatusError, APIConnectionError
from PIL import Image


def check_images(client):
    options={'model':config.IMAGE_MODEL,'size':'1024x1024','quality':'low','output_format':'png'}
    generated=client.images.generate(prompt='A brass key on an ivory background, editorial illustration, no text.',**options)
    data=base64.b64decode(generated.data[0].b64_json,validate=True)
    Image.open(io.BytesIO(data)).verify()
    edited=client.images.edit(image=('reference.png',data,'image/png'),prompt='Preserve the key and framing. Change only the background to sage green. No text.',**options)
    Image.open(io.BytesIO(base64.b64decode(edited.data[0].b64_json,validate=True))).verify()
    print('Image generation and reference editing verified:',config.IMAGE_MODEL)


def main():
    parser=argparse.ArgumentParser(description=__doc__)
    parser.add_argument('--images',action='store_true',help='Verify actual image generation and editing (two paid calls).')
    args=parser.parse_args();config.preflight()
    try:
        c=OpenAI(timeout=180 if args.images else 20,max_retries=0)
        print('Text model listed:',c.models.retrieve(config.TEXT_MODEL).id)
        if args.images:
            check_images(c)
        else:
            try:print('Image model listed:',c.models.retrieve(config.IMAGE_MODEL).id)
            except APIStatusError as error:
                if error.status_code!=404:raise
                print('Image model is not in the model catalog; endpoint availability is unverified.')
                print('Run with --images to check generation and editing directly (two paid calls).')
                return 3
        return 0
    except APIStatusError as error:
        print('Provider HTTP status:',error.status_code)
        return 1
    except APIConnectionError:
        print('Provider connection failed')
        return 2


if __name__=='__main__':sys.exit(main())
