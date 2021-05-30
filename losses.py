# -------------------------------------------------------------------
# Copyright (C) 2020 Harbin Institute of Technology, China
# Author: Xudong Lv (15B901019@hit.edu.cn)
# Released under Creative Commons
# Attribution-NonCommercial-ShareAlike 4.0 International License.
# http://creativecommons.org/licenses/by-nc-sa/4.0/
# -------------------------------------------------------------------

import torch
from torch import nn as nn

from quaternion_distances import quaternion_distance
from utils import quat2mat, rotate_back, rotate_forward, tvector2mat, quaternion_from_matrix


class GeometricLoss(nn.Module):
    def __init__(self):
        super().__init__()
        self.sx = torch.nn.Parameter(torch.Tensor([0.0]), requires_grad=True)
        self.sq = torch.nn.Parameter(torch.Tensor([-3.0]), requires_grad=True)
        self.transl_loss = nn.SmoothL1Loss(reduction='none')

    def forward(self, target_transl, target_rot, transl_err, rot_err):
        loss_transl = self.transl_loss(transl_err, target_transl).sum(1).mean()
        loss_rot = quaternion_distance(rot_err, target_rot, rot_err.device).mean()
        total_loss = torch.exp(-self.sx) * loss_transl + self.sx
        total_loss += torch.exp(-self.sq) * loss_rot + self.sq
        return total_loss


class ProposedLoss(nn.Module):
    def __init__(self, rescale_trans, rescale_rot):
        super(ProposedLoss, self).__init__()
        self.rescale_trans = rescale_trans
        self.rescale_rot = rescale_rot
        self.transl_loss = nn.SmoothL1Loss(reduction='none')
        self.losses = {}

    def forward(self, target_transl, target_rot, transl_err, rot_err):
        loss_transl = 0.
        if self.rescale_trans != 0.:
            loss_transl = self.transl_loss(transl_err, target_transl).sum(1).mean() * 100
        loss_rot = 0.
        if self.rescale_rot != 0.:
            loss_rot = quaternion_distance(rot_err, target_rot, rot_err.device).mean()
        total_loss = self.rescale_trans*loss_transl + self.rescale_rot*loss_rot
        self.losses['total_loss'] = total_loss
        self.losses['transl_loss'] = loss_transl
        self.losses['rot_loss'] = loss_rot
        return self.losses


class L1Loss(nn.Module):
    def __init__(self, rescale_trans, rescale_rot):
        super(L1Loss, self).__init__()
        self.rescale_trans = rescale_trans
        self.rescale_rot = rescale_rot
        self.transl_loss = nn.SmoothL1Loss(reduction='none')

    def forward(self, target_transl, target_rot, transl_err, rot_err):
        loss_transl = self.transl_loss(transl_err, target_transl).sum(1).mean()
        loss_rot = self.transl_loss(rot_err, target_rot).sum(1).mean()
        total_loss = self.rescale_trans*loss_transl + self.rescale_rot*loss_rot
        return total_loss


class DistancePoints3D(nn.Module):
    def __init__(self):
        super(DistancePoints3D, self).__init__()

    def forward(self, point_clouds, target_transl, target_rot, transl_err, rot_err):
        """
        Points Distance Error
        Args:
            point_cloud: list of B Point Clouds, each in the relative GT frame
            transl_err: network estimate of the translations
            rot_err: network estimate of the rotations

        Returns:
            The mean distance between 3D points
        """
        #start = time.time()
        total_loss = torch.tensor([0.0]).to(transl_err.device)
        for i in range(len(point_clouds)):
            point_cloud_gt = point_clouds[i].to(transl_err.device)
            point_cloud_out = point_clouds[i].clone()

            R_target = quat2mat(target_rot[i])
            T_target = tvector2mat(target_transl[i])
            RT_target = torch.mm(T_target, R_target)

            R_predicted = quat2mat(rot_err[i])
            T_predicted = tvector2mat(transl_err[i])
            RT_predicted = torch.mm(T_predicted, R_predicted)

            RT_total = torch.mm(RT_target.inverse(), RT_predicted)

            point_cloud_out = rotate_forward(point_cloud_out, RT_total)

            error = (point_cloud_out - point_cloud_gt).norm(dim=0)
            error.clamp(100.)
            total_loss += error.mean()

        #end = time.time()
        #print("3D Distance Time: ", end-start)

        return total_loss/target_transl.shape[0]


# The combination of L1 loss of translation part,
# quaternion angle loss of rotation part,
# distance loss of the pointclouds
class CombinedLoss(nn.Module):
    def __init__(self, rescale_trans, rescale_rot, weight_point_cloud):
        super(CombinedLoss, self).__init__()
        self.rescale_trans = rescale_trans
        self.rescale_rot = rescale_rot
        self.transl_loss = nn.SmoothL1Loss(reduction='none')
        self.weight_point_cloud = weight_point_cloud
        self.loss = {}

    def forward(self, point_clouds, target_transl, target_rot, transl_err, rot_err):
        """
        The Combination of Pose Error and Points Distance Error
        Args:
            point_cloud: list of B Point Clouds, each in the relative GT frame
            target_transl: groundtruth of the translations
            target_rot: groundtruth of the rotations
            transl_err: network estimate of the translations
            rot_err: network estimate of the rotations

        Returns:
            The combination loss of Pose error and the mean distance between 3D points
        """
        loss_transl = 0.
        if self.rescale_trans != 0.:
            loss_transl = self.transl_loss(transl_err, target_transl).sum(1).mean()
        loss_rot = 0.
        if self.rescale_rot != 0.:
            loss_rot = quaternion_distance(rot_err, target_rot, rot_err.device).mean()
        pose_loss = self.rescale_trans*loss_transl + self.rescale_rot*loss_rot

        #start = time.time()
        point_clouds_loss = torch.tensor([0.0]).to(transl_err.device)
        for i in range(len(point_clouds)):
            point_cloud_gt = point_clouds[i].to(transl_err.device)
            point_cloud_out = point_clouds[i].clone()

            R_target = quat2mat(target_rot[i])
            T_target = tvector2mat(target_transl[i])
            RT_target = torch.mm(T_target, R_target)

            R_predicted = quat2mat(rot_err[i])
            T_predicted = tvector2mat(transl_err[i])
            RT_predicted = torch.mm(T_predicted, R_predicted)

            RT_total = torch.mm(RT_target.inverse(), RT_predicted)

            point_cloud_out = rotate_forward(point_cloud_out, RT_total)

            error = (point_cloud_out - point_cloud_gt).norm(dim=0)
            error.clamp(100.)
            point_clouds_loss += error.mean()

        #end = time.time()
        #print("3D Distance Time: ", end-start)
        total_loss = (1 - self.weight_point_cloud) * pose_loss +\
                     self.weight_point_cloud * (point_clouds_loss/target_transl.shape[0])
        self.loss['total_loss'] = total_loss
        self.loss['transl_loss'] = loss_transl
        self.loss['rot_loss'] = loss_rot
        self.loss['point_clouds_loss'] = point_clouds_loss/target_transl.shape[0]

        return self.loss


# Stereo-supervised Loss
class StereoLoss(nn.Module):
    def __init__(self, rescale_trans, rescale_rot):
        super(StereoLoss, self).__init__()
        self.rescale_trans = rescale_trans
        self.rescale_rot = rescale_rot
        self.transl_loss = nn.SmoothL1Loss(reduction='none')
        self.loss = {}

    def forward(self, T_init_left, T_init_right, T23_gt, transl_err, rot_err, b_size):
        """
        The Stereo-supervised Loss
        Args:
            T_init_left:   Initial extrinsic from camera02 to Lidar
            T_init_right:  Initial extrinsic from camera03 to Lidar
            T23:           Extrinsic from camera02 to camera03
            transl_err:    Network estimate of the translations
            rot_err:       Network estimate of the rotations

        Returns:
            The loss of Pose error
        """
        transl_err_left = transl_err[:b_size]
        transl_err_right = transl_err[b_size:]
        rot_err_left = rot_err[:b_size]
        rot_err_right = rot_err[b_size:]

        tr_pred_list = []
        rot_pred_list = []
        tr_gt_list = []
        rot_gt_list = []

        for i in range(b_size):
            pred_R_left = quat2mat(rot_err_left[i])
            pred_t_left = tvector2mat(transl_err_left[i])
            pred_left_RT = torch.mm(pred_t_left, pred_R_left)

            pred_R_right = quat2mat(rot_err_right[i])
            pred_t_right = tvector2mat(transl_err_right[i])
            pred_right_RT = torch.mm(pred_t_right, pred_R_right)

            T_cam2_lidar = torch.mm(pred_left_RT.inverse(), T_init_left[i])
            T_cam3_lidar = torch.mm(pred_right_RT.inverse(), T_init_right[i])

            T_23_pred = torch.mm(T_cam2_lidar, T_cam3_lidar.inverse())

            q_pred = quaternion_from_matrix(T_23_pred)
            t_pred = T_23_pred[:3, 3]

            q_gt = quaternion_from_matrix(T23_gt[i])
            t_gt = T23_gt[i][:3, 3]

            tr_pred_list.append(t_pred)
            rot_pred_list.append(q_pred)
            tr_gt_list.append(t_gt)
            rot_gt_list.append(q_gt)

        tr_pred = torch.stack(tr_pred_list)
        rot_pred = torch.stack(rot_pred_list)
        tr_gt = torch.stack(tr_gt_list)
        rot_gt = torch.stack(rot_gt_list)

        loss_transl = 0.
        if self.rescale_trans != 0.:
            loss_transl = self.transl_loss(tr_pred, tr_gt).sum(1).mean()
        loss_rot = 0.
        if self.rescale_rot != 0.:
            loss_rot = quaternion_distance(rot_pred, rot_gt, rot_err.device).mean()
        total_loss = self.rescale_trans*loss_transl + self.rescale_rot*loss_rot

        self.loss['total_loss'] = total_loss
        self.loss['transl_loss'] = loss_transl
        self.loss['rot_loss'] = loss_rot

        return self.loss
