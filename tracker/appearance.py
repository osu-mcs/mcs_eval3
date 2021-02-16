from argparse import ArgumentParser
import pickle
import gzip
from pathlib import Path
import os
from .utils import draw_bounding_boxes, draw_appearance_bars, split_obj_masks, get_obj_position, get_mask_box

from .track import track_objects
import torch
import numpy as np
from torch.utils.data import Dataset, DataLoader

from torch.optim import Adam
from torchvision import transforms
import torch.nn as nn
import cv2
import webcolors
import sys

class ObjectFeatures():
    def __init__(self, kp, des):
        self.keypoints = kp
        self.descriptors = des

class ObjectDataset():
    def __init__(self, data, transform=None):
        self.data = data
        self.shape_labels = sorted(set(data['shapes']))
        self.color_labels = sorted(set(np.array(data['textures']).squeeze(1)))

        self.data['shapes'] = np.array(self.data['shapes'])
        self.data['color'] = np.array(data['textures']).squeeze(1)

        for shape_id, shape in enumerate(self.shape_labels):
            self.data['shapes'][self.data['shapes'] == shape] = shape_id

        for color_id, color in enumerate(self.color_labels):
            self.data['color'][self.data['color'] == color] = color_id

        self.data['shapes'] = self.data['shapes'].astype(np.int)
        self.data['color'] = self.data['color'].astype(np.int)
        self.transform = transform

    def shape_label_name(self, label_id):
        return self.shape_labels[label_id]

    def shape_labels_count(self):
        _labels, counts = np.unique(self.data['shapes'], return_counts=True)
        return [counts[_labels == label_i][0] for label_i, _ in enumerate(self.shape_labels)]

    def color_label_name(self, label_id):
        return self.color_labels[label_id]

    def color_labels_count(self):
        _labels, counts = np.unique(self.data['color'], return_counts=True)
        return [counts[_labels == label_i][0] for label_i, _ in enumerate(self.shape_labels)]
    
    def __len__(self):
        return len(self.data['images'])

    def __getitem__(self, idx):
        if torch.is_tensor(idx):
            idx = idx.tolist()

        sample = {'images': self.data['images'][idx],
                  'shapes': self.data['shapes'][idx],
                  'color': self.data['color'][idx]}

        if self.transform:
            sample['gray_images'] = rgb_to_grayscale(torch.FloatTensor(sample['images']))

        return sample

class AppearanceMatchModel():
    def __init__(self):
        ### ATTRIBUTES LEARNED DURING TRAINING ###

        # The learned amount of leeway in feature match distance
        # to allow without raising an appearance mismatch
        self.feature_match_slack = 0
        self.obj_dictionary = dict()    # The dictionary of learned objects and their keypoints and descriptors
        
        ### ATTRIBUTES USED IN TESTING/EVAL ###

        # The object's features from the previous frame,
        # in case no robust feature match can be detected
        # using the learned object dictionary and the robot
        # must fall back to frame-by-frame matching.
        # self.prev_obj_features = dict()
        self.detector = cv2.SIFT()      # The SIFT feature detection object
        FLANN_INDEX_KDTREE = 0
        index_params = dict(algorithm=FLANN_INDEX_KDTREE, trees=5)
        search_params = dict(checks=50)
        self.flann = cv2.FlannBasedMatcher(index_params, search_params)       # FLANN based matcher

    # Search the learned object space to identify what the initial object is
    def identifyInitialObject(self, img_kp, img_des):
        # NOTE: If object descriptors do not sufficiently match up with any other object, return None
        # (system will fall back to frame-by-frame matching)
        match_avgs = dict()
        for obj_id, obj in self.obj_dictionary.items():
            o_match_rates = list()
            for o_img in obj:
                o_kp = o_img.keypoints
                o_des = o_img.descriptors
                o_matches = self.flann.knnMatch(img_des, o_des, k=2)
                
                o_good = list()
                for m, n in o_matches:
                    if m.distance < 0.7 * n.distance:
                        o_good.append([m])
                o_match_rates.append(len(o_good) / len(o_matches))

            o_match_rates = np.array(o_match_rates)
            o_match_avg = np.mean(o_match_rates)
            match_avgs[obj_id] = o_match_avg
        max_o = max(match_avgs, key=lambda o: match_avgs[o])
        return max_o if match_avgs[max_o] >= 1 - self.feature_match_slack else None

    # Match feature descriptors
    def detectFeatureMatch(self, img_kp, img_des, obj):
        shape_id = obj['base_image']['shape_id']
        if shape_id is None:
            match = self.frameMatch(img_kp, img_des, obj)  # fall back to frame-by-frame feature matching
        else:
            # feature match
            # avg_match_rate = 1     # TODO: Implement good match rate (see OpenCV Python SIFT docs)
            l_match_rates = list()
            for l_obj_img in self.obj_dictionary[shape_id]:
                l_matches = self.flann.knnMatch(img_des, l_obj_img.descriptors, k=2)

                l_good = list()
                for m, n in l_matches:
                    if m.distance < 0.7 * n.distance:
                        l_good.append([m])
                l_match_rates.append(len(l_good) / len(l_matches))
            
            l_match_rates = np.array(l_match_rates)
            avg_match_rate = np.mean(l_match_rates)
            if avg_match_rate >= 1 - self.feature_match_slack:
                match = True
            else:
                match = self.frameMatch(img_kp, img_des, obj)  # fall back to frame-by-frame feature matching
            
        return obj, match

    # Determine feature match with the state of the object in the previous frame
    def frameMatch(self, img_kp, img_des, obj):
        prev_kp = obj['appearance']['keypoint_history'][-1]
        prev_des = obj['appearance']['descriptor_history'][-1]
        f_matches = self.flann.knnMatch(img_des, prev_des, k=2) # k=2 so we can apply the ratio test next
        f_good = list()
        for m, n in f_matches:
            if m.distance < 0.7 * n.distance:
                f_good.append([m])
        
        return len(f_good) / len(f_matches) >= 1 - self.feature_match_slack

    # Check for any appearance mismatches in the provided images
    def match(self, image, objects_info, device='cpu', level='level2'):
        for key, obj in objects_info.items():
            if not obj['visible']:
                continue
            top_x, top_y, bottom_x, bottom_y = obj['bounding_box']
            obj_current_image = image.crop((top_y, top_x, bottom_y, bottom_x))

            image_area = np.prod(obj_current_image.size)
            base_image = np.array(image)
            mask_image = np.zeroes(obj['mask'].shape, dtype=base_image.dtype)
            mask_image[obj['mask']] = 255

            # obj_clr_hist_0 = cv2.calcHist([np.array(image)], [0], mask_image, [10], [0, 256])
            # obj_clr_hist_1 = cv2.calcHist([np.array(image)], [1], mask_image, [10], [0, 256])
            # obj_clr_hist_2 = cv2.calcHist([np.array(image)], [2], mask_image, [10], [0, 256])
            # obj_clr_hist = (obj_clr_hist_0 + obj_clr_hist_1 + obj_clr_hist_2) / 3

            # run SIFT on image
            img_kp, img_des = self.detector.detectAndCompute(obj_current_image, None)

            if 'base_image' not in obj.keys() or (len(obj['position_history'] < 5) and obj['base_image']['image_area'] < image_area):
                obj['base_image'] = dict()
                obj['base_image']['shape_id'] = self.identifyInitialObject(img_kp, img_des)
                obj['appearance']['feature_history'] = dict()
                obj['appearance']['feature_history']['keypoints'] = list()
                obj['appearance']['feature_history']['descriptors'] = list()
                # obj['base_image']['histogram'] = obj_clr_hist
                obj['appearance'] = dict()

            # Run detectFeatureMatch
            obj, feature_match = self.detectFeatureMatch(img_kp, img_des, obj)

            # Update feature match indicator if the object is not occluded
            if not obj['occluded']:
                obj['appearance']['feature_history']['keypoints'].append(img_kp)
                obj['appearance']['feature_history']['descriptors'].append(img_des)
                obj['appearance']['match'] = feature_match

            if 'mismatch_count' not in obj['appearance']:
                obj['appearance']['mismatch_count'] = 0

            if obj['appearance']['match']:
                obj['appearance']['mismatch_count'] = 0
            else:
                obj['appearance']['mismatch_count'] += 1

        return objects_info


    def train(self):
        pass

    def test(self, dataloader):
        # batch_acc = { 'shape': list(), 'color': list() }
        batch_acc = list()
        for i, batch in enumerate(dataloader):
            object_image = batch['images']
            object_gray_image = batch['gray_images']
            object_shape = batch['shapes']
            object_color = batch['color']
            
            object_image_kp, object_image_des = self.detector.detectAndCompute(object_image, None)
            
            l_match_rates = list()
            for l_obj_img in self.obj_dictionary[object_shape]:
                l_matches = self.flann.knnMatch(object_image_des, l_obj_img.descriptors, k=2)

                l_good = list()
                for m, n in l_matches:
                    if m.distance < 0.7 * n.distance:
                        l_good.append([m])
                l_match_rates.append(len(l_good) / len(l_matches))
            
            l_match_rates = np.array(l_match_rates)
            avg_match_rate = np.mean(l_match_rates)
            batch_acc.append(avg_match_rate)
        
        print('Accuracy:', np.mean(batch_acc))
            
        pass

    def process_video(self, video_data, save_path=None, save_mp4=False, save_gif=False, device='cpu'):
        track_info = dict()
        processed_frames = list()
        violation = False
        for frame_num, frame in enumerate(video_data):
            track_info = track_objects(frame.obj_mask, track_info)
            track_info['objects'] = self.match(frame.image, track_info['objects'], device)

            if save_gif:
                img = draw_bounding_boxes(frame.image, track_info['objects'])
                img = draw_appearance_bars(img, track_info['objects'])
                processed_frames.append(img)
            
            if 'objects' in track_info and len(track_info['objects'].keys()) > 0:
                for o in track_info['objects'].values():
                    if not o['appearance']['match']:
                        # print('Appearance Mismatch')
                        violation = True
        
        # save gif
        if save_gif:
            processed_frames[0].save(save_path + '.gif', save_all=True,
                                    append_images=processed_frames[1:], optimize=False, loop=1)

        # save video
        if save_gif and save_mp4:
            import moviepy.editor as mp
            clip = mp.VideoFileClip(save_path + '.gif')
            clip.write_videofile(save_path + '.mp4')
            os.remove(save_path + '.gif')

        return violation

# Convert the image to a Tensor
def obj_image_to_tensor(obj_image, gray=False):
    obj_image = obj_image.resize((50, 50))
    obj_image = np.array(obj_image)
    obj_image = obj_image.reshape((3, 50, 50))
    obj_image = torch.Tensor(obj_image).float()
    if gray:
        obj_image = rgb_to_grayscale(obj_image)
    
    return obj_image

# Convert a color image to grayscale
def rgb_to_grayscale(img, num_output_channels: int = 1):
    if num_output_channels not in (1, 3):
        raise ValueError('num_output_channels should be either 1 or 3')

    r, g, b = img.unbind(dim=-3)
    l_img = (0.2989 * r + 0.587 * g + 0.114 * b).to(img.dtype)
    l_img = l_img.unsqueeze(dim=-3)

    if num_output_channels == 3:
        return l_img.expand(img.shape)

    return l_img

def make_parser():
    parser = ArgumentParser()
    parser.add_argument('--test-scenes-path', required=True, type=Path)
    parser.add_argument('--train-scenes-path', required=True, type=Path)
    parser.add_argument('--train-dataset-path', required=False, type=Path,
                        default=os.path.join(os.getcwd(), 'train_object_dataset.p'))
    parser.add_argument('--test-dataset-path', required=False, type=Path,
                        default=os.path.join(os.getcwd(), 'test_object_dataset.p'))
    parser.add_argument('--results-dir', required=False, type=Path, default=os.path.join(os.getcwd(), 'results'))
    parser.add_argument('--run', required=False, type=int, default=1)
    parser.add_argument('--lr', required=False, type=float, default=0.001)
    parser.add_argument('--epochs', required=False, type=int, default=50)
    parser.add_argument('--checkpoint-interval', required=False, type=int, default=1)
    parser.add_argument('--log-interval', required=False, type=int, default=1)
    parser.add_argument('--opr', choices=['generate_dataset', 'train', 'test', 'demo'], default='demo',
                        help='operation (opr) to be performed')
    return parser

if __name__ == '__main__':
    from torch.utils.tensorboard import SummaryWriter
    from tqdm import tqdm

    args = make_parser().parse_args()
    args.device = 'cuda' if torch.cuda_is_available() else 'cpu'

    # paths
    experiment_path = os.path.join(args.results_dir, 'run_{}'.format(args.run), )
    os.makedirs(experiment_path, exist_ok=True)
    log_path = os.path.join(experiment_path, 'logs')
    model_path = os.path.join(experiment_path, 'model.p')
    checkpoint_path = os.path.join(experiment_path, 'checkpoint.p')

    # Determine the operation to be performed
    if args.opr == 'generate_dataset':
        pass    # TODO: Gulshan is working on dataset generation

    elif args.opr == 'train':
        pass    # TODO: Gulshan is working on training

    elif args.opr == 'test':
        train_object_dataset = ObjectDataset(pickle.load(open(args.train_dataset_path, 'rb')),
                                             transform=transforms.Compose([transforms.Grayscale()]))
        dataloader = DataLoader(train_object_dataset, batch_size=args.batch_size, shuffle=False, num_workers=1)
        model = pickle.load(model_path)
        # model.load_state_dict(torch.load(model_path, map_location=torch.device('cpu')))
        # model = model.to(args.device)

        model.test(dataloader)

    elif args.opr == 'demo':
        all_scenes = list(args.train_scenes_path.glob('*.pkl.gz'))
        print(f'Found {len(all_scenes)} scenes')

        model = pickle.load(model_path)

        violations = list()
        np.random.seed(0)
        np.random.shuffle(all_scenes)
        mismatch_cases = list()

        for idx, scene_file in enumerate(all_scenes):
            with gzip.open(scene_file, 'rb') as fd:
                scene_data = pickle.load(fd)
            
            print(f'{idx:} {scene_file.name}')
            v = process_video()

            if v:
                mismatch_cases.append(scene_file)
            violations.append(v)
        print(mismatch_cases)
        print((len(violations) - sum(violations)) / len(violations))





