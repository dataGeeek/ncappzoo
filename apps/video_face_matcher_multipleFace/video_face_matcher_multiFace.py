#! /usr/bin/env python3

# Copyright(c) 2017 Intel Corporation. 
# License: MIT See LICENSE file in root directory.


import argparse
import cv2
from mvnc import mvncapi as mvnc
import numpy
import os
import sys
from time import localtime, strftime

EXAMPLES_BASE_DIR = '../../'
IMAGES_DIR = './'

# name of the opencv window
CV_WINDOW_NAME = "FaceNet- Multiple people"

CAMERA_INDEX = 0
REQUEST_CAMERA_WIDTH = 640
REQUEST_CAMERA_HEIGHT = 480

# the same face will return 0.0
# different faces return higher numbers
# this is NOT between 0.0 and 1.0


# Run an inference on the passed image
# image_to_classify is the image on which an inference will be performed
#    upon successful return this image will be overlayed with boxes
#    and labels identifying the found objects within the image.
# ssd_mobilenet_graph is the Graph object from the NCAPI which will
#    be used to peform the inference.
def run_inference(image_to_classify, facenet_graph):
    # get a resized version of the image that is the dimensions
    # SSD Mobile net expects
    resized_image = preprocess_image(image_to_classify)

    # ***************************************************************
    # Send the image to the NCS
    # ***************************************************************
    facenet_graph.LoadTensor(resized_image.astype(numpy.float16), None)

    # ***************************************************************
    # Get the result from the NCS
    # ***************************************************************
    output, userobj = facenet_graph.GetResult()

    return output


# overlays the boxes and labels onto the display image.
# display_image is the image on which to overlay to
# image info is a text string to overlay onto the image.
# matching is a Boolean specifying if the image was a match.
# returns None
def overlay_on_image(display_image, image_info, matching):
    rect_width = 10
    offset = int(rect_width / 2)
    if image_info is not None:
        cv2.putText(display_image, image_info, (30, 30), cv2.FONT_HERSHEY_SIMPLEX, 0.5, (255, 0, 0), 1)
    if matching:
        # match, green rectangle
        cv2.rectangle(display_image, (0 + offset, 0 + offset),
                      (display_image.shape[1] - offset - 1, display_image.shape[0] - offset - 1),
                      (0, 255, 0), 10)
    else:
        # not a match, red rectangle
        cv2.rectangle(display_image, (0 + offset, 0 + offset),
                      (display_image.shape[1] - offset - 1, display_image.shape[0] - offset - 1),
                      (0, 0, 255), 10)


# whiten an image
def whiten_image(source_image):
    source_mean = numpy.mean(source_image)
    source_standard_deviation = numpy.std(source_image)
    std_adjusted = numpy.maximum(source_standard_deviation, 1.0 / numpy.sqrt(source_image.size))
    whitened_image = numpy.multiply(numpy.subtract(source_image, source_mean), 1 / std_adjusted)
    return whitened_image


# create a preprocessed image from the source image that matches the
# network expectations and return it
def preprocess_image(src):
    # scale the image
    NETWORK_WIDTH = 160
    NETWORK_HEIGHT = 160
    preprocessed_image = cv2.resize(src, (NETWORK_WIDTH, NETWORK_HEIGHT))

    # convert to RGB
    preprocessed_image = cv2.cvtColor(preprocessed_image, cv2.COLOR_BGR2RGB)

    # whiten
    preprocessed_image = whiten_image(preprocessed_image)

    # return the preprocessed image
    return preprocessed_image


# determine if two images are of matching faces based on the
# the network output for both images.
def face_match(face1_output, face2_output):
    if len(face1_output) != len(face2_output):
        print('length mismatch in face_match')
        return False
    total_diff = 0
    for output_index in range(0, len(face1_output)):
        this_diff = numpy.square(face1_output[output_index] - face2_output[output_index])
        total_diff += this_diff
    print('Total Difference is: ' + str(total_diff))
    return total_diff


# handles key presses
# raw_key is the return value from cv2.waitkey
# returns False if program should end, or True if should continue
def handle_keys(raw_key):
    ascii_code = raw_key & 0xFF
    if (ascii_code == ord('q')) or (ascii_code == ord('Q')):
        return False

    return True


# start the opencv webcam streaming and pass each frame
# from the camera to the facenet network for an inference
# Continue looping until the result of the camera frame inference
# matches the valid face output and then return.
# valid_output is inference result for the valid image
# validated image filename is the name of the valid image file
# graph is the ncsdk Graph object initialized with the facenet graph file
#   which we will run the inference on.
# returns None
def run_camera(valid_output, validated_image_list, graph, capture_path, classifier_path, threshold, window):
    camera_device = cv2.VideoCapture(CAMERA_INDEX)
    camera_device.set(cv2.CAP_PROP_FRAME_WIDTH, REQUEST_CAMERA_WIDTH)
    camera_device.set(cv2.CAP_PROP_FRAME_HEIGHT, REQUEST_CAMERA_HEIGHT)

    actual_camera_width = camera_device.get(cv2.CAP_PROP_FRAME_WIDTH)
    actual_camera_height = camera_device.get(cv2.CAP_PROP_FRAME_HEIGHT)
    print('actual camera resolution: ' + str(actual_camera_width) + ' x ' + str(actual_camera_height))

    if (camera_device is None) or (not camera_device.isOpened()):
        print('Could not open camera.  Make sure it is plugged in.')
        print('Also, if you installed python opencv via pip or pip3 you')
        print('need to uninstall it and install from source with -D WITH_V4L=ON')
        print('Use the provided script: install-opencv-from_source.sh')
        return

    frame_count = 0

    cv2.namedWindow(CV_WINDOW_NAME)
    face_cascade = cv2.CascadeClassifier(classifier_path)

    found_match = False

    while True:
        # Read image from camera,
        ret_val, vid_image = camera_device.read()
        if not ret_val:
            print("No image from camera, exiting")
            break

        frame_count += 1
        frame_name = 'camera frame ' + str(frame_count)
        faces = find_any_face(vid_image, face_cascade)

        # if no face found by haar classifier, just print the raw image, and continue with next image.
        # If one is found, run comparison with known faces.
        if len(faces) == 0 and window:
            if print_image_and_wait_for_exit(vid_image):
                break
            print('Haar Classifier did not found any face')
            continue

        elif len(faces) == 0 and not window:
            print('Haar Classifier did not found any face')
            continue

        else:
            for (x, y, w, h) in faces:
                cv2.rectangle(vid_image, (x, y), (x + w, y + h), (0, 255, 0), 2)

        # run a single inference on the image and overwrite the
        # boxes and labels
        test_output = run_inference(vid_image, graph)

        min_distance = 100
        min_index = -1

        for i in range(0, len(valid_output)):
            distance = face_match(valid_output[i], test_output)
            if distance < min_distance:
                min_distance = distance
                min_index = i

        if min_distance <= threshold:
            print('PASS!  File ' + frame_name + ' matches ' + validated_image_list[min_index])
            found_match = True

        else:
            found_match = False
            print('FAIL!  File ' + frame_name + ' does not match any image.')
            save_image(vid_image, capture_path)

        overlay_on_image(vid_image, frame_name, found_match)

        if print_image_and_wait_for_exit(vid_image):
            break

    if found_match and window:
        cv2.imshow(CV_WINDOW_NAME, vid_image)
        cv2.waitKey(0)


def find_any_face(image, classifier):
    gray = cv2.cvtColor(image, cv2.COLOR_BGR2GRAY)
    return classifier.detectMultiScale(gray, scaleFactor=1.2, minNeighbors=5, minSize=(30, 30))


def save_image(image, path):
    photo = (os.path.dirname(os.path.realpath(__file__))
             + path
             + 'image_'
             + strftime('%Y_%m_%d_%H_%M_%S', localtime()) + '.jpg')
    cv2.imwrite(photo, image)

    # check if the window is visible, this means the user hasn't closed
    # the window via the X button
    # display the results and wait for user to hit a key


def print_image_and_wait_for_exit(image):
    prop_val = cv2.getWindowProperty(CV_WINDOW_NAME, cv2.WND_PROP_ASPECT_RATIO)
    if prop_val < 0.0:
        print('window closed')
        return True
    cv2.imshow(CV_WINDOW_NAME, image)
    raw_key = cv2.waitKey(1)
    if raw_key != -1:
        if not handle_keys(raw_key):
            print('user pressed Q')
            return True


# This function is called from the entry point to do
# all the work of the program
def main():
    parser = argparse.ArgumentParser(
        description="Pragram to detect unknown faces and captures them")

    parser.add_argument('-c', '--capture', type=str,
                        default='~/capture/',
                        help="Path where pictures of unknown faces shall be stored.")

    parser.add_argument('-cl', '--classifier', type=str,
                        default='casc.xml',
                        help="Path where haar classifier is located.")

    parser.add_argument('-g', '--graph', type=str,
                        default='facenet_celeb_ncs.graph',
                        help="Path to graph file.")

    parser.add_argument('-t', '--threshold', type=float,
                        default=0.8,
                        help="PThreshold for classifying an face as similar enough.")

    parser.add_argument('-v', '--validated', type=str,
                        default='./validated_images/',
                        help="Path to folder with validated images")

    parser.add_argument('-w', '--window', type=bool,
                        default=True,
                        help="Run program with window.")

    args = parser.parse_args()

    # Get a list of ALL the sticks that are plugged in
    # we need at least one
    devices = mvnc.EnumerateDevices()
    if len(devices) == 0:
        print('No NCS devices found')
        quit()

    # Pick the first stick to run the network
    device = mvnc.Device(devices[0])

    # Open the NCS
    device.OpenDevice()

    # The graph file that was created with the ncsdk compiler
    graph_file_name = args.graph

    # read in the graph file to memory buffer
    with open(graph_file_name, mode='rb') as f:
        graph_in_memory = f.read()

    # create the NCAPI graph instance from the memory buffer containing the graph file.
    graph = device.AllocateGraph(graph_in_memory)

    validated_image_list = os.listdir(args.validated)
    valid_output = []
    for i in validated_image_list:
        validated_image = cv2.imread(args.validated + i)
        valid_output.append(run_inference(validated_image, graph))

    run_camera(valid_output, validated_image_list, graph, args.capture, args.classifier, args.threshold, args.window)

    # Clean up the graph and the device
    graph.DeallocateGraph()
    device.CloseDevice()


# main entry point for program. we'll call main() to do what needs to be done.
if __name__ == "__main__":
    sys.exit(main())
