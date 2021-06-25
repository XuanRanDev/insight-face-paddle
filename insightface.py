# Copyright (c) 2021 PaddlePaddle Authors. All Rights Reserved.
#
# Licensed under the Apache License, Version 2.0 (the "License");
# you may not use this file except in compliance with the License.
# You may obtain a copy of the License at
#
#     http://www.apache.org/licenses/LICENSE-2.0
#
# Unless required by applicable law or agreed to in writing, software
# distributed under the License is distributed on an "AS IS" BASIS,
# WITHOUT WARRANTIES OR CONDITIONS OF ANY KIND, either express or implied.
# See the License for the specific language governing permissions and
# limitations under the License.

import os
import argparse
import requests
import logging
import imghdr
import pickle
import tarfile
from functools import partial

import cv2
import numpy as np
from tqdm import tqdm
from prettytable import PrettyTable
from PIL import Image, ImageDraw
import paddle
from paddle.inference import Config
from paddle.inference import create_predictor

__all__ = ['PaddleFace']
BASE_INFERENCE_MODEL_DIR = os.path.expanduser("~/.insightface/ppmodels/")
BASE_DOWNLOAD_URL = "https://paddle-model-ecology.bj.bcebos.com/model/insight-face/{}.tar"


def parser(add_help=True):
    def str2bool(v):
        return v.lower() in ("true", "t", "1")

    parser = argparse.ArgumentParser(add_help=add_help)
    parser.add_argument(
        "--det_model",
        type=str,
        default="BlazeFace",
        help="The detection model.")
    parser.add_argument(
        "--rec_model",
        type=str,
        default="MobileFace",
        help="The recognition model.")
    parser.add_argument(
        "--use_gpu",
        type=str2bool,
        default=True,
        help="Whether use GPU to predict. Default by True.")
    parser.add_argument(
        "--enable_mkldnn",
        type=str2bool,
        default=False,
        help="Whether use MKLDNN to predict, valid only when --use_gpu is False. Default by False."
    )
    parser.add_argument(
        "--cpu_threads",
        type=int,
        default=1,
        help="The num of threads with CPU, valid only when --use_gpu is False. Default by 1."
    )
    # parser.add_argument("--image", type=str, help="The path or directory of image file(s) to be predicted.")
    # parser.add_argument("--video", type=str, help="The path of video file to be predicted, valid only when --image is not specified.")
    parser.add_argument(
        "--input",
        type=str,
        help="The path or directory of image(s) or video to be predicted.")
    parser.add_argument(
        "--output", type=str, help="The directory of prediction result.")
    parser.add_argument(
        "--det", action="store_true", help="Whether to detect.")
    parser.add_argument(
        "--threshold",
        type=float,
        default=0.5,
        help="The threshold of detection postprocess. Default by 0.5.")
    parser.add_argument(
        "--rec", action="store_true", help="Whether to recognize.")
    parser.add_argument(
        "--base_lib",
        type=str,
        default=None,
        help="The path of base lib file.")
    parser.add_argument(
        "--cdd_num",
        type=int,
        default=10,
        help="The number of candidates in the recognition retrieval. Default by 10."
    )
    parser.add_argument(
        "--max_batch_size",
        type=int,
        default=1,
        help="The maxium of batch_size to recognize. Default by 1.")
    parser.add_argument(
        "--build_lib",
        type=str,
        default=None,
        help="The base lib path to build.")
    parser.add_argument(
        "--img_dir",
        type=str,
        default=None,
        help="The img(s) dir used to build base lib.")
    parser.add_argument(
        "--label",
        type=str,
        default=None,
        help="The label file path used to build base lib.")

    return parser


def print_config(args):
    args = vars(args)
    table = PrettyTable(['Param', 'Value'])
    for param in args:
        table.add_row([param, args[param]])
    width = len(str(table).split("\n")[0])
    print("{}".format("-" * width))
    print("PaddleFace".center(width))
    print(table)
    print("Powered by PaddlePaddle!".rjust(width))
    print("{}".format("-" * width))


def download_with_progressbar(url, save_path):
    """Download from url with progressbar.
    """
    if os.path.isfile(save_path):
        os.remove(save_path)
    response = requests.get(url, stream=True)
    total_size_in_bytes = int(response.headers.get("content-length", 0))
    block_size = 1024  # 1 Kibibyte
    progress_bar = tqdm(total=total_size_in_bytes, unit="iB", unit_scale=True)
    with open(save_path, "wb") as file:
        for data in response.iter_content(block_size):
            progress_bar.update(len(data))
            file.write(data)
    progress_bar.close()
    if total_size_in_bytes == 0 or progress_bar.n != total_size_in_bytes or not os.path.isfile(
            save_path):
        raise Exception(
            f"Something went wrong while downloading model/image from {url}")


def check_model_file(model):
    """Check the model files exist and download and untar when no exist. 
    """
    model_map = {
        "ArcFace": "arcface_iresnet50_v1.0_infer",
        "BlazeFace": "blazeface_fpn_ssh_1000e_v1.0_infer",
        "MobileFace": "mobileface_v1.0_infer"
    }

    if os.path.isdir(model):
        model_file_path = os.path.join(model, "inference.pdmodel")
        params_file_path = os.path.join(model, "inference.pdiparams")
        if not os.path.exists(model_file_path) or not os.path.exists(
                params_file_path):
            raise Exception(
                f"The specifed model directory error. The drectory must include 'inference.pdmodel' and 'inference.pdiparams'."
            )

    elif model in model_map:
        storage_directory = partial(os.path.join, BASE_INFERENCE_MODEL_DIR,
                                    model)
        url = BASE_DOWNLOAD_URL.format(model_map[model])

        tar_file_name_list = [
            "inference.pdiparams", "inference.pdiparams.info",
            "inference.pdmodel"
        ]
        model_file_path = storage_directory("inference.pdmodel")
        params_file_path = storage_directory("inference.pdiparams")
        if not os.path.exists(model_file_path) or not os.path.exists(
                params_file_path):
            tmp_path = storage_directory(url.split("/")[-1])
            logging.info(f"Download {url} to {tmp_path}")
            os.makedirs(storage_directory(), exist_ok=True)
            download_with_progressbar(url, tmp_path)
            with tarfile.open(tmp_path, "r") as tarObj:
                for member in tarObj.getmembers():
                    filename = None
                    for tar_file_name in tar_file_name_list:
                        if tar_file_name in member.name:
                            filename = tar_file_name
                    if filename is None:
                        continue
                    file = tarObj.extractfile(member)
                    with open(storage_directory(filename), "wb") as f:
                        f.write(file.read())
            os.remove(tmp_path)
        if not os.path.exists(model_file_path) or not os.path.exists(
                params_file_path):
            raise Exception(
                f"Something went wrong while downloading and unzip the model[{model}] files!"
            )
    else:
        raise Exception(
            f"The specifed model name error. Support 'BlazeFace' for detection and 'ArcFace' and 'MobileFace' for recognition. And support local directory that include model files ('inference.pdmodel' and 'inference.pdiparams')."
        )

    return model_file_path, params_file_path


def normalize_image(img, scale=None, mean=None, std=None, order='chw'):
    if isinstance(scale, str):
        scale = eval(scale)
    scale = np.float32(scale if scale is not None else 1.0 / 255.0)
    mean = mean if mean is not None else [0.485, 0.456, 0.406]
    std = std if std is not None else [0.229, 0.224, 0.225]

    shape = (3, 1, 1) if order == 'chw' else (1, 1, 3)
    mean = np.array(mean).reshape(shape).astype('float32')
    std = np.array(std).reshape(shape).astype('float32')

    if isinstance(img, Image.Image):
        img = np.array(img)

    assert isinstance(img, np.ndarray), "invalid input 'img' in NormalizeImage"
    return (img.astype('float32') * scale - mean) / std


def to_CHW_image(img):
    if isinstance(img, Image.Image):
        img = np.array(img)
    return img.transpose((2, 0, 1))


class ColorMap(object):
    def __init__(self, num):
        super().__init__()
        self.get_color_map_list(num)
        self.color_map = {}
        self.ptr = 0

    def __getitem__(self, key):
        return self.color_map[key]

    def update(self, keys):
        for key in keys:
            if key not in self.color_map:
                i = self.ptr % len(self.color_list)
                self.color_map[key] = self.color_list[i]
                self.ptr += 1

    def get_color_map_list(self, num_classes):
        color_map = num_classes * [0, 0, 0]
        for i in range(0, num_classes):
            j = 0
            lab = i
            while lab:
                color_map[i * 3] |= (((lab >> 0) & 1) << (7 - j))
                color_map[i * 3 + 1] |= (((lab >> 1) & 1) << (7 - j))
                color_map[i * 3 + 2] |= (((lab >> 2) & 1) << (7 - j))
                j += 1
                lab >>= 3
        self.color_list = [
            color_map[i:i + 3] for i in range(0, len(color_map), 3)
        ]


class ImageReader(object):
    def __init__(self, inputs):
        super().__init__()
        self.idx = 0
        if isinstance(inputs, np.ndarray):
            self.image_list = [inputs]
        else:
            imgtype_list = {'jpg', 'bmp', 'png', 'jpeg', 'rgb', 'tif', 'tiff'}
            self.image_list = []
            if os.path.isfile(inputs):
                if imghdr.what(inputs) not in imgtype_list:
                    raise Exception(
                        f"Error type of input path, only support: {imgtype_list}"
                    )
                self.image_list.append(inputs)
            elif os.path.isdir(inputs):
                tmp_file_list = os.listdir(inputs)
                warn_tag = False
                for file_name in tmp_file_list:
                    file_path = os.path.join(inputs, file_name)
                    if not os.path.isfile(file_path):
                        warn_tag = True
                        continue
                    if imghdr.what(file_path) in imgtype_list:
                        self.image_list.append(file_path)
                    else:
                        warn_tag = True
                if warn_tag:
                    logging.warning(
                        f"The directory of input contine directory or not supported file type, only support: {imgtype_list}"
                    )
            else:
                raise Exception("The input path is not exist!")

    def __iter__(self):
        return self

    def __next__(self):
        if self.idx >= len(self.image_list):
            raise StopIteration

        data = self.image_list[self.idx]
        if isinstance(data, np.ndarray):
            self.idx += 1
            return data, "tmp.png"
        path = data
        _, file_name = os.path.split(path)
        img = cv2.imread(path)
        if img is None:
            logging.warning(f"Error in reading image: {path}! Ignored.")
            self.idx += 1
            return self.__next__()
        img = cv2.cvtColor(img, cv2.COLOR_BGR2RGB)
        self.idx += 1
        return img, file_name

    def __len__(self):
        return len(self.image_list)


class VideoReader(object):
    def __init__(self, inputs):
        super().__init__()
        videotype_list = {"mp4"}
        if os.path.splitext(inputs)[-1][1:] not in videotype_list:
            raise Exception(
                f"The input file is not supported, only support: {videotype_list}"
            )
        self.capture = cv2.VideoCapture(inputs)
        self.file_name = os.path.split(inputs)[-1]

    def get_info(self):
        info = {}
        width = int(self.capture.get(cv2.CAP_PROP_FRAME_WIDTH))
        height = int(self.capture.get(cv2.CAP_PROP_FRAME_HEIGHT))
        fourcc = cv2.VideoWriter_fourcc(* 'mp4v')
        info["file_name"] = self.file_name
        info["fps"] = 30
        info["shape"] = (width, height)
        info["fourcc"] = cv2.VideoWriter_fourcc(* 'mp4v')
        return info

    def __iter__(self):
        return self

    def __next__(self):
        ret, frame = self.capture.read()
        if not ret:
            raise StopIteration
        return frame, self.file_name


class ImageWriter(object):
    def __init__(self, output_dir):
        super().__init__()
        if output_dir is None:
            raise Exception(
                "Please specify the directory of saving prediction results by --output."
            )
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        self.output_dir = output_dir

    def write(self, image, file_name):
        path = os.path.join(self.output_dir, file_name)
        cv2.imwrite(path, cv2.cvtColor(image, cv2.COLOR_RGB2BGR))


class VideoWriter(object):
    def __init__(self, output_dir, video_info):
        super().__init__()
        if output_dir is None:
            raise Exception(
                "Please specify the directory of saving prediction results by --output."
            )
        if not os.path.exists(output_dir):
            os.makedirs(output_dir)
        output_path = os.path.join(output_dir, video_info["file_name"])
        fourcc = cv2.VideoWriter_fourcc(* 'mp4v')
        self.writer = cv2.VideoWriter(output_path, video_info["fourcc"],
                                      video_info["fps"], video_info["shape"])

    def write(self, frame, file_name):
        self.writer.write(frame)

    def __del__(self):
        if hasattr(self, "writer"):
            self.writer.release()


class BasePredictor(object):
    def __init__(self, predictor_config):
        super().__init__()
        self.predictor_config = predictor_config
        self.predictor, self.input_names, self.output_names = self.load_predictor(
            predictor_config["model_file"], predictor_config["params_file"])

    def load_predictor(self, model_file, params_file):
        config = Config(model_file, params_file)
        if self.predictor_config["use_gpu"]:
            config.enable_use_gpu(200, 0)
            config.switch_ir_optim(True)
        else:
            config.disable_gpu()
            config.set_cpu_math_library_num_threads(self.predictor_config[
                "cpu_threads"])

            if self.predictor_config["enable_mkldnn"]:
                try:
                    # cache 10 different shapes for mkldnn to avoid memory leak
                    config.set_mkldnn_cache_capacity(10)
                    config.enable_mkldnn()
                except Exception as e:
                    logging.error(
                        "The current environment does not support `mkldnn`, so disable mkldnn."
                    )
        config.disable_glog_info()
        config.enable_memory_optim()
        # use zero copy
        config.switch_use_feed_fetch_ops(False)
        predictor = create_predictor(config)
        input_names = predictor.get_input_names()
        output_names = predictor.get_output_names()
        return predictor, input_names, output_names

    def preprocess(self):
        raise NotImplementedError

    def postprocess(self):
        raise NotImplementedError

    def predict(self, img):
        raise NotImplementedError


class Detector(BasePredictor):
    def __init__(self, det_config, predictor_config):
        super().__init__(predictor_config)
        self.det_config = det_config

    def preprocess(self, img):
        img = normalize_image(
            img,
            scale=1. / 255.,
            mean=[0.485, 0.456, 0.406],
            std=[0.229, 0.224, 0.225],
            order='hwc')
        img_info = {}
        img_info["im_shape"] = np.array(
            img.shape[:2], dtype=np.float32)[np.newaxis, :]
        img = img.transpose((2, 0, 1)).copy()
        img_info["image"] = img[np.newaxis, :, :, :]
        img_info["scale_factor"] = np.array(
            [1., 1.], dtype=np.float32)[np.newaxis, :]
        return img_info

    def postprocess(self, np_boxes):
        expect_boxes = (np_boxes[:, 1] > self.det_config["threshold"]) & (
            np_boxes[:, 0] > -1)
        return np_boxes[expect_boxes, :]

    def predict(self, img):
        inputs = self.preprocess(img)
        for input_name in self.input_names:
            input_tensor = self.predictor.get_input_handle(input_name)
            input_tensor.copy_from_cpu(inputs[input_name])
        self.predictor.run()
        output_tensor = self.predictor.get_output_handle(self.output_names[0])
        np_boxes = output_tensor.copy_to_cpu()
        # boxes_num = self.detector.get_output_handle(self.detector_output_names[1])
        # np_boxes_num = boxes_num.copy_to_cpu()
        box_list = self.postprocess(np_boxes)
        return box_list


class Recognizer(BasePredictor):
    def __init__(self, rec_config, predictor_config):
        super().__init__(predictor_config)
        if rec_config["base_lib"] is not None:
            if rec_config["build_lib"] is not None:
                raise Exception(
                    "Only one of base_lib and build_lib can be set!")
            self.load_base_lib(rec_config["base_lib"])
        elif rec_config["build_lib"] is None:
            raise Exception("One of base_lib and build_lib have to be set!")
        self.rec_config = rec_config

    def preprocess(self, img, box_list=None):
        img = normalize_image(
            img,
            scale=1. / 255.,
            mean=[0.5, 0.5, 0.5],
            std=[0.5, 0.5, 0.5],
            order='hwc')
        if box_list is None:
            height, width = img.shape[:2]
            box_list = [[0, 0, 0, 0, width, height]]
        batch = []
        input_batches = []
        cnt = 0
        for idx, box in enumerate(box_list):
            box[2:][box[2:] < 0] = 0
            xmin, ymin, xmax, ymax = list(map(int, box[2:]))
            face_img = img[ymin:ymax, xmin:xmax, :]
            face_img = cv2.resize(face_img, (112, 112)).transpose(
                (2, 0, 1)).copy()
            batch.append(face_img)
            cnt += 1
            if cnt % self.rec_config["max_batch_size"] == 0 or (
                    idx + 1) == len(box_list):
                input_batches.append(np.array(batch))
                batch = []
        return input_batches

    def postprocess(self):
        pass

    def match(self, np_feature):
        base_feature = np.array(self.base_lib["feature"]).squeeze()
        labels = []
        for feature in np_feature:
            diff = np.subtract(base_feature, feature)
            dist = np.sum(np.square(diff), -1)
            candidate_idx = np.argpartition(
                dist, range(10))[:self.rec_config["cdd_num"]]
            candidate_label_list = list(
                np.array(self.base_lib["label"])[candidate_idx])
            maxlabel = max(candidate_label_list,
                           key=candidate_label_list.count)
            labels.append(maxlabel)
        return labels

    def load_base_lib(self, file_path):
        with open(file_path, "rb") as f:
            self.base_lib = pickle.load(f)

    def predict(self, img, box_list=None):
        batch_list = self.preprocess(img, box_list)
        feature_list = []
        for batch in batch_list:
            for input_name in self.input_names:
                input_tensor = self.predictor.get_input_handle(input_name)
                input_tensor.copy_from_cpu(batch)
            self.predictor.run()
            output_tensor = self.predictor.get_output_handle(self.output_names[
                0])
            np_feature = output_tensor.copy_to_cpu()
            feature_list.append(np_feature)
        return np.array(feature_list)


class InsightFace(object):
    def __init__(self, args, print_info=True):
        super().__init__()
        if not (args.det or args.rec) and not args.build_lib:
            raise Exception(
                "Specify at least the detection(--det) or recognition(--rec) or --build_lib!"
            )

        if print_info:
            print_config(args)

        if args.build_lib:
            if args.img_dir is None or args.label is None:
                raise Exception(
                    "Please specify the --img_dir and --label when build base lib."
                )
            args.det, args.rec = True, True

        self.args = args

        predictor_config = {
            "use_gpu": args.use_gpu,
            "enable_mkldnn": args.enable_mkldnn,
            "cpu_threads": args.cpu_threads
        }
        if args.det:
            model_file_path, params_file_path = check_model_file(
                args.det_model)
            det_config = {"threshold": args.threshold}
            predictor_config["model_file"] = model_file_path
            predictor_config["params_file"] = params_file_path
            self.det_predictor = Detector(det_config, predictor_config)
            self.color_map = ColorMap(100)

        if args.rec:
            model_file_path, params_file_path = check_model_file(
                args.rec_model)
            rec_config = {
                "max_batch_size": args.max_batch_size,
                "resize": 112,
                "base_lib": args.base_lib,
                "build_lib": args.build_lib,
                "cdd_num": args.cdd_num
            }
            predictor_config["model_file"] = model_file_path
            predictor_config["params_file"] = params_file_path
            self.rec_predictor = Recognizer(rec_config, predictor_config)

    def preprocess(self, img):
        img = img.astype(np.float32, copy=False)
        return img

    def draw(self, img, box_list, labels):
        self.color_map.update(labels)
        im = Image.fromarray(img)
        draw_thickness = min(im.size) // 320
        draw = ImageDraw.Draw(im)

        for i, dt in enumerate(box_list):
            bbox, score = dt[2:], dt[1]
            label = labels[i]
            color = tuple(self.color_map[label])

            xmin, ymin, xmax, ymax = bbox
            # draw bbox
            draw.line(
                [(xmin, ymin), (xmin, ymax), (xmax, ymax), (xmax, ymin),
                 (xmin, ymin)],
                width=draw_thickness,
                fill=color)

            # draw label
            text = "{} {:.4f}".format(label, score)
            tw, th = draw.textsize(text)
            draw.rectangle(
                [(xmin + 1, ymin - th), (xmin + tw + 1, ymin)], fill=color)
            draw.text((xmin + 1, ymin - th), text, fill=(255, 255, 255))
        return np.array(im)

    def predict_np_img(self, img):
        input_img = self.preprocess(img)
        box_list = None
        np_feature = None
        if hasattr(self, "det_predictor"):
            box_list = self.det_predictor.predict(input_img)
        if hasattr(self, "rec_predictor"):
            np_feature = self.rec_predictor.predict(input_img, box_list)
        return box_list, np_feature

    def predict(self, input_data):
        if isinstance(input_data, np.ndarray):
            self.input_reader = ImageReader(input_data)
            self.output_writer = ImageWriter(self.args.output)
        elif isinstance(input_data, str):
            if input_data.endswith('mp4'):
                self.input_reader = VideoReader(input_data)
                info = self.input_reader.get_info()
                self.output_writer = VideoWriter(self.args.output, info)
            else:
                self.input_reader = ImageReader(input_data)
                self.output_writer = ImageWriter(self.args.output)
        else:
            raise Exception(
                f"The input data error. Only support path of image or video(.mp4) and dirctory that include images."
            )

        for img, file_name in self.input_reader:
            if img is None:
                logging.warning(f"Error in reading img {file_name}! Ignored.")
                continue
            box_list, np_feature = self.predict_np_img(img)
            if np_feature is not None:
                labels = self.rec_predictor.match(np_feature)
            else:
                labels = ["face"] * len(box_list)
            if box_list is not None:
                result = self.draw(img, box_list, labels=labels)
                self.output_writer.write(result, file_name)
            logging.info(f"File: {file_name}, predict label(s): {labels}")
        logging.info(f"Predict complete!")

    def build_lib(self):
        img_dir = self.args.img_dir
        label_path = self.args.label
        with open(label_path, "r") as f:
            sample_list = f.readlines()

        feature_list = []
        label_list = []

        for idx, sample in enumerate(sample_list):
            name, label = sample.strip().split("\t")
            img = cv2.imread(os.path.join(img_dir, name))
            if img is None:
                logging.warning(f"Error in reading img {name}! Ignored.")
                continue
            box_list, np_feature = self.predict_np_img(img)
            if len(box_list) < 1:
                logging.warning(f"No face detected in img {name}")
                continue
            feature = np_feature[0]
            max_area = 0
            for i, box in enumerate(box_list):
                xmin, ymin, xmax, ymax = box[2:]
                area = (ymax - ymin) * (xmax - xmin)
                if area > max_area:
                    feature = np_feature[i]
            feature_list.append(feature)
            label_list.append(label)

            if idx % 1000 == 0:
                logging.info(f"Idx: {idx}")

        with open(self.args.build_lib, 'wb') as f:
            pickle.dump({"label": label_list, "feature": feature_list}, f)


# for CLI
def main(args=None):
    logging.basicConfig(level=logging.INFO)

    args = parser().parse_args()
    predictor = InsightFace(args)
    if args.build_lib:
        predictor.build_lib()
    else:
        predictor.predict(args.input)


if __name__ == "__main__":
    args = parser().parse_args()
    main(args)
