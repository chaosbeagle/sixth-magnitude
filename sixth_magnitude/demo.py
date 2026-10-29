"""Deterministic workflow fixtures. These are diagrams, never patient images."""
from pathlib import Path
import random
from PIL import Image,ImageDraw

def create_fixture(path,profile):
    path=Path(path)
    if profile=='generic':
        im=Image.new('RGB',(1600,1000),'#e8ecef');d=ImageDraw.Draw(im)
        d.rounded_rectangle((110,120,1490,870),radius=40,fill='#bdcbd0',outline='#637b85',width=5)
        for y in range(200,800,100):
            for x in range(220,1450,140):
                d.ellipse((x-24,y-24,x+24,y+24),fill='#e9f1ef',outline='#5c777d',width=3)
        d.line([(520,350),(560,395),(540,435),(590,480),(568,525)],fill='#263a49',width=9)
        d.ellipse((1030,635,1120,705),fill='#b66f42',outline='#8d563a',width=3)
        d.text((120,55),'SYNTHETIC SURFACE / workflow fixture / two drawn marks',fill='#263a49',font_size=28)
        d.text((120,918),'Pixel coordinates. Inspect a region and its surrounding context.',fill='#263a49',font_size=24)
    else:
        im=Image.new('RGB',(2048,1536),'#fff8fb');d=ImageDraw.Draw(im);rng=random.Random(73)
        d.ellipse((160,150,1910,1380),fill='#f0cfdf',outline='#a8699a',width=6)
        for _ in range(160):
            x,y=rng.randint(350,1690),rng.randint(350,1180)
            rx,ry=rng.randint(18,65),rng.randint(16,45)
            d.ellipse((x-rx,y-ry,x+rx,y+ry),fill='#fff6f8',outline='#9a658e',width=9)
        d.rectangle((1320,500,1660,900),fill='#a46c9c')
        d.text((180,55),'SYNTHETIC PATHOLOGY WORKFLOW FIXTURE',fill='#503a61',font_size=34)
        d.text((180,1435),'Diagram only / no diagnostic labels / simulated scale: 4 micrometres per pixel',fill='#503a61',font_size=26)
    im.save(path)
