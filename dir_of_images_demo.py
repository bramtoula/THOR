# --------------------------------------------------------
# THOR
# Licensed under The MIT License
# Written by Axel Sauer (axel.sauer@tum.de)
# --------------------------------------------------------

import pdb
import argparse
import cv2
import os
import numpy as np
import sys
from imutils.video import FPS
import json
import glob
from trackers.tracker import SiamFC_Tracker, SiamRPN_Tracker, SiamMask_Tracker
from benchmark.bench_utils.bbox_helper import cxy_wh_2_rect, xyxy_to_xywh

# constants
BRIGHTGREEN = [102, 255, 0]
RED = [0, 0, 255]
YELLOW = [0, 255, 255]
np.set_printoptions(precision=6, suppress=True)

OUTPUT_WIDTH = 640
OUTPUT_HEIGHT = 480
PADDING = 2

parser = argparse.ArgumentParser(description='Images from dir Test')
parser.add_argument('-t', '--tracker', dest='tracker', required=True,
                    help='Name of the tracker [SiamFC, SiamRPN, SiamMask]')
parser.add_argument('-d', '--dir', dest='images_dir', required=True,
                    help='Path to the directory containing the images')
parser.add_argument('-p', '--prefix_img_name', dest='img_prefix', required=False, default='rgb_',
                    help='Characters before the image ID in the name of the image file')
parser.add_argument('--vanilla', action='store_true',
                    help='run the tracker without memory')
parser.add_argument('-v', '--viz', action='store_true',
                    help='whether visualize result')
parser.add_argument('--verbose', action='store_true',
                    help='print info about temp mem')
parser.add_argument('--lb_type', type=str, default='ensemble',
                    help='Specify the type of lower bound [dynamic, ensemble]')

drawnBox = np.zeros(4)
boxToDraw = np.zeros(4)
mousedown = False
mouseupdown = False
initialize = False


def on_mouse(event, x, y, flags, params):
    global mousedown, mouseupdown, drawnBox, boxToDraw, initialize, boxToDraw_xywh
    if event == cv2.EVENT_LBUTTONDOWN:
        drawnBox[[0, 2]] = x
        drawnBox[[1, 3]] = y
        mousedown = True
        mouseupdown = False
    elif mousedown and event == cv2.EVENT_MOUSEMOVE:
        drawnBox[2] = x
        drawnBox[3] = y
    elif event == cv2.EVENT_LBUTTONUP:
        drawnBox[2] = x
        drawnBox[3] = y
        mousedown = False
        mouseupdown = True
        initialize = True
    boxToDraw = drawnBox.copy()
    boxToDraw[[0, 2]] = np.sort(boxToDraw[[0, 2]])
    boxToDraw[[1, 3]] = np.sort(boxToDraw[[1, 3]])
    boxToDraw_xywh = xyxy_to_xywh(boxToDraw)


def bb_on_im(im, location, mask):
    location = [int(l) for l in location]  #

    if len(mask):
        im[:, :, 2] = mask * 255 + (1 - mask) * im[:, :, 2]

    # prediction
    cv2.rectangle(im, (location[0], location[1]),
                  (location[0] + location[2], location[1] + location[3]),
                  (0, 255, 255), 3)

    return im


def show_images(images_paths, tracker, mirror=False, viz=False):
    global initialize

    # vs = cv2.VideoCapture(0)
    cv2.namedWindow('Images', cv2.WINDOW_NORMAL)
    cv2.resizeWindow('Images', OUTPUT_WIDTH, OUTPUT_HEIGHT)
    cv2.setMouseCallback('Images', on_mouse, 0)

    outputBoxToDraw = None
    bbox = None
    fps = None
    state = None
    mask = []

    bounding_boxes = []

    # Stay on first image until bounding box is added
    im = cv2.imread(images_paths[0])
    if mirror:
            im = cv2.flip(im, 1)

    # loop over video stream ims
    done = False
    initial_bb_selected = False
    nb_images_seen = 0
    while not done:
        if not initial_bb_selected:
            im = cv2.imread(images_paths[0])
        else:
            im = cv2.imread(images_paths[nb_images_seen])
            nb_images_seen += 1

            if (nb_images_seen == len(images_paths)):
                done = True

        if mirror:
            im = cv2.flip(im, 1)

        if mousedown:
            (x1, y1, x2, y2) = [int(l) for l in boxToDraw]
            cv2.rectangle(im, (x1, y1), (x2, y2),
                          BRIGHTGREEN, PADDING)

        elif mouseupdown:
            if initialize:
                init_pos = boxToDraw_xywh[[0, 1]]
                init_sz = boxToDraw_xywh[[2, 3]]
                state = tracker.setup(im, init_pos, init_sz)
                initialize = False
                fps = FPS().start()
                initial_bb_selected = True
            else:
                state = tracker.track(im, state)
                location = cxy_wh_2_rect(
                    state['target_pos'], state['target_sz'])
                (cx, cy, w, h) = [int(l) for l in location]
                bounding_boxes.append(location)

                fps.update()
                fps.stop()

                # Display the image
                info = [
                    ("Score:", f"{state['score']:.4f}"),
                    ("FPS:", f"{fps.fps():.2f}"),
                ]

                if not state['score'] > 0.8:
                    info.insert(0, ("Object lost since", ""))
                else:
                    if 'mask' in state.keys():
                        mask = state['mask'] > state['p'].seg_thr
                    im = bb_on_im(im, location, mask)

                for (i, (k, v)) in enumerate(info):
                    text = "{}: {}".format(k, v)
                    cv2.putText(im, text, (10, OUTPUT_HEIGHT - ((i * 20) + 20)),
                                cv2.FONT_HERSHEY_SIMPLEX, 0.6, (0, 0, 255), 2)

        cv2.imshow("Images", im)

        # check for escape key
        key = cv2.waitKey(1)
        if key == 27 or key == 1048603:
            break

    # release the pointer
    cv2.destroyAllWindows()
    return bounding_boxes


def load_cfg(args):
    json_path = f"configs/{args.tracker}/VOT2018_"
    if args.vanilla:
        json_path += "vanilla.json"
    else:
        json_path += f"THOR_{args.lb_type}.json"
    cfg = json.load(open(json_path))
    return cfg


def generate_label_file(output_dir, bounding_boxes, images_paths):
    results_path = output_dir+'/results_tracking.txt'
    with open(results_path, 'w') as f:
        for i in range(len(images_paths)):
            (cx, cy, w, h) = bounding_boxes[i]
            f.write(images_paths[i].split('/')[-1].strip('.png')+" 1.0 "+str(cx-w/2)+' '+str(cy-h/2)+' '+str(cx+w/2)+' '+str(cy+h/2)+'\n')

if __name__ == '__main__':
    args = parser.parse_args()

    cfg = load_cfg(args)
    cfg['THOR']['viz'] = args.viz
    cfg['THOR']['verbose'] = args.verbose

    print("[INFO] Initializing the tracker.")
    if args.tracker == 'SiamFC':
        tracker = SiamFC_Tracker(cfg)
    elif args.tracker == 'SiamRPN':
        tracker = SiamRPN_Tracker(cfg)
    elif args.tracker == 'SiamMask':
        tracker = SiamMask_Tracker(cfg)
    elif args.tracker == 'SiamRPN_PP':
        tracker = SiamRPN_PP_Tracker(cfg)
    else:
        raise ValueError(f"Tracker {args.tracker} does not exist.")

    filenames = glob.glob(args.images_dir+"/*.png")
    nb_chars_prefix = len(args.img_prefix)
    filenames_ids = [int(file.split('/')[-1][nb_chars_prefix:-4])
                     for file in filenames]
    filenames_ids.sort()
    images_paths = [args.images_dir+'/'+args.img_prefix +
                               str(filename_id)+'.png' for filename_id in filenames_ids]

    print("[INFO] Starting video stream.")
    bounding_boxes = show_images(images_paths, tracker, mirror=True, viz=args.viz)

    output_dir = args.images_dir+'_tracked_boxes'
    if not os.path.exists(output_dir):
        os.makedirs(output_dir)

    generate_label_file(output_dir,bounding_boxes,images_paths)
        

