# from depth_anything_wrapper import DepthAnythingWrapper
# from grounded_sam_wrapper import GroundedSamWrapper
from ROS_handler import ROSHandler
from camera_handler import ImageSubscriber
from geometry_msgs.msg import TransformStamped, Pose
from tf.transformations import quaternion_matrix, quaternion_from_matrix
import numpy as np
import rospy
import cv2

import random

camera_intrinsics = (203.71833, 203.71833, 319.5, 239.5)


class MyClass:
    def __init__(self):
        # self.depth_anything_wrapper = DepthAnythingWrapper(intrinsics=camera_intrinsics)
        # self.grounded_sam_wrapper = GroundedSamWrapper()
        pass

    def get_stamped_transform(self, translation, rotation):
        transform_stamped = TransformStamped()
        transform_stamped.transform.translation.x = translation[0]
        transform_stamped.transform.translation.y = translation[1]
        transform_stamped.transform.translation.z = translation[2]

        transform_stamped.transform.rotation.x = rotation[0]
        transform_stamped.transform.rotation.y = rotation[1]
        transform_stamped.transform.rotation.z = rotation[2]
        transform_stamped.transform.rotation.w = rotation[3]
        return transform_stamped
    
    def process_image(self, image, transform, show=False):
        print('Processing image...')

        depth = self.depth_anything_wrapper.get_depth_map(image)
        mask = self.grounded_sam_wrapper.get_mask(image)[0][0]
        depth_masked = self.grounded_sam_wrapper.mask_depth_map(depth, mask)
        pointcloud_masked = self.depth_anything_wrapper.get_pointcloud(depth_masked)
        pointcloud_masked_world = self.depth_anything_wrapper.transform_pointcloud_to_world(pointcloud_masked, transform)

        if show:
            self.grounded_sam_wrapper.show_mask(mask, title="Original Mask")
            self.depth_anything_wrapper.show_depth_map(depth_masked, title="Original Depth Map Masked")
            self.depth_anything_wrapper.show_pointclouds([pointcloud_masked], title="Original Pointcloud Masked in Camera Frame")
            self.depth_anything_wrapper.show_pointclouds_with_frames_and_grid([pointcloud_masked_world], [transform], title="Original Pointcloud Masked in World Frame")

        print('Done')
        return depth, mask, depth_masked, pointcloud_masked, pointcloud_masked_world
    
    def estimate_scale_shift(self, data1, data2, transform1, transform2, show=False):
        print('Estimating scale and shift...')
        _, mask1, depth_masked1, _, pointcloud_masked_world1 = data1
        _, mask2, _, _, _ = data2

        max_inliers_counter = 0
        best_alpha, best_beta = 0, 0
        best_pointcloud_world = None
        best_projection = None
        num_skips = 0
        # Loop N times
        for i in range(1000):
            # Pick two random points from pointcloud 1
            pointA_world = random.choice(pointcloud_masked_world1.points)
            pointB_world = random.choice(pointcloud_masked_world1.points)
            if np.all(pointA_world == pointB_world):
                num_skips += 1
                continue

            # Project the points into coordinates of camera 2
            projectionA_cam2 = self.depth_anything_wrapper.project_point_from_world(pointA_world, transform2)
            projectionB_cam2 = self.depth_anything_wrapper.project_point_from_world(pointB_world, transform2)

            # Project the pointcloud into coordinates of camera 2
            projected_pointcloud_depth, projected_pointcloud_mask_2 = self.depth_anything_wrapper.project_pointcloud(pointcloud_masked_world1, transform2)
            # self.grounded_sam_wrapper.show_mask_and_points(projected_pointcloud_mask_2, [projectionA_cam2, projectionB_cam2], title="Pointcloud 1 projected onto camera 2 with rand points")

            # Get the closest points between single point (from pointcloud 1) and mask 2
            closestA_cam2 = self.grounded_sam_wrapper.get_closest_point(mask2, projectionA_cam2)
            closestB_cam2 = self.grounded_sam_wrapper.get_closest_point(mask2, projectionB_cam2)

            # Show the closest points
            # self.grounded_sam_wrapper.show_mask_and_points(mask2, [closestA_cam2, closestB_cam2], title="Mask 2 in camera 2 with closest points")
            # self.grounded_sam_wrapper.show_mask_and_points(projected_pointcloud_mask_2, [closestA_cam2, closestB_cam2], title="Pointcloud 1 projected onto camera 2 with closest points")

            # self.grounded_sam_wrapper.show_mask_and_points(mask2, [closestA_cam2, projectionA_cam2], title="projection and closest point A")
            # self.grounded_sam_wrapper.show_mask_and_points(mask2, [closestB_cam2, projectionB_cam2], title="projection and closest point B")

            # Triangulate
            trtiangulatedA_world = self.depth_anything_wrapper.triangulate(pointA_world, transform1, closestA_cam2, transform2)
            trtiangulatedB_world = self.depth_anything_wrapper.triangulate(pointB_world, transform1, closestB_cam2, transform2)
            # print(f'trtiangulatedA_world: {trtiangulatedA_world}')
            # print(f'trtiangulatedB_world: {trtiangulatedB_world}')
            try:
                # Project the triangulated points into coordinates of camera 2
                projected_triangulatedA_cam2 = self.depth_anything_wrapper.project_point_from_world(trtiangulatedA_world, transform1)
                projected_triangulatedB_cam2 = self.depth_anything_wrapper.project_point_from_world(trtiangulatedB_world, transform1)
            except Exception as e:
                # print(f'{e}, Skipping to next iteration')
                num_skips += 1
                continue
            
            # # Show mask 2 and triangulated points
            # self.grounded_sam_wrapper.show_mask_and_points(mask2, [projected_triangulatedA_cam2, projected_triangulatedB_cam2], title="Mask 2 with triangulated points")
            # # Show pointcloud 1 and triangulated points in camera 2
            # self.grounded_sam_wrapper.show_mask_and_points(projected_pointcloud_mask_2, [projected_triangulatedA_cam2, projected_triangulatedB_cam2], title="Pointcloud 1 projected onto camera 2 with triangulated points")
            
            # Transform triangulated point into camera 1 coordinates
            triangulated1_A = self.depth_anything_wrapper.transform_point_from_world(trtiangulatedA_world, transform1)
            triangulated1_B = self.depth_anything_wrapper.transform_point_from_world(trtiangulatedB_world, transform1)

            # Get  distance of original pointA_world and pointB_world
            z_A_orig = self.depth_anything_wrapper.transform_point_from_world(pointA_world, transform1)[2]
            z_B_orig = self.depth_anything_wrapper.transform_point_from_world(pointB_world, transform1)[2]

            # Get distance id triangulated point
            z_A_triangulated = triangulated1_A[2]
            z_B_triangulated = triangulated1_B[2]
            # print(f'depth_A_orig: {z_A_orig}')
            # print(f'depth_A_triangulated: {z_A_triangulated}')
            # print(f'depth_B_orig: {z_B_orig}')
            # print(f'depth_B_triangulated: {z_B_triangulated}')
            
            # Calculate scale and shift
            alpha = (z_A_triangulated - z_B_triangulated) / (z_A_orig - z_B_orig)
            beta = z_A_triangulated - alpha * z_A_orig
            # print(f'alpha: {alpha:.2f}, beta: {beta:.2f}')

            # Scale and shift
            depth_new = self.depth_anything_wrapper.scale_depth_map(depth_masked1, scale=alpha, shift=beta)

            # Show original and scaled depth maps
            # self.depth_anything_wrapper.show_depth_map(depth_masked1, title="Original depth map")
            # self.depth_anything_wrapper.show_depth_map(depth_new, title="Scaled depth map")

            # Get scaled/shifted pointcloud
            new_pc_cam1 = self.depth_anything_wrapper.get_pointcloud(depth_new)

            # Transform scaled pointcloud into world coordinates
            new_pc_world = self.depth_anything_wrapper.transform_pointcloud_to_world(new_pc_cam1, transform1)

            # Get projection of scaled pointcloud into camera 2
            projection_new_pc2_depth, projection_new_pc2_mask = self.depth_anything_wrapper.project_pointcloud(new_pc_world, transform2)
        
            # Show projection of scaled pointcloud in camera 2 and closest points
            # self.grounded_sam_wrapper.show_mask_and_points(projection_new_pc2_depth, [closestA_cam2, closestB_cam2], title="Scaled pointcloud with closest points")

            # # Show original and scaled depth maps and masks
            # self.grounded_sam_wrapper.show_masks([projection_new_pc2_mask, mask2], title="Scaled depth map and mask")
            # # Show original and scaled pointclouds in world coordinates
            # self.depth_anything_wrapper.show_pointclouds_with_frames([pointcloud_masked_world1, new_pc_world], [transform1], title="Original and scaled pointcloud")

            # Count the number of inliers between mask 2 and projection of scaled pointcloud
            num_inliers = self.depth_anything_wrapper.count_inliers(projection_new_pc2_mask, mask2)
            # print(f'{i}: num_inliers: {num_inliers}')

            if num_inliers > max_inliers_counter:
                max_inliers_counter = num_inliers
                best_alpha = alpha
                best_beta = beta
                best_pointcloud_world = new_pc_world
                best_projection = projection_new_pc2_depth

        print(f'Max inliers: {max_inliers_counter}, alpha: {best_alpha:.2f}, beta: {best_beta:.2f}, Skipped points: {num_skips}')

        if show: 
            # Show original and scaled depth maps and masks
            self.grounded_sam_wrapper.show_masks([best_projection, mask2], title="Scaled depth map and mask")
            # Show original and scaled pointclouds in world coordinates
            self.depth_anything_wrapper.show_pointclouds_with_frames([pointcloud_masked_world1, best_pointcloud_world], [transform1], title="Original and scaled pointcloud")

        return best_alpha, best_beta, best_pointcloud_world
    
    def get_desired_pose(self, position):
        # build Pose
        p = Pose()
        p.position.x = float(position[0])
        p.position.y = float(position[1])
        p.position.z = float(position[2])
        p.orientation.x = float(1)
        p.orientation.y = float(0)
        p.orientation.z = float(0)
        p.orientation.w = float(0)
        return p


def test1():
    my_class = MyClass()
    ros_handler = ROSHandler()

    image1 = cv2.imread(f'/root/workspace/images/moves/cable0.jpg')
    image2 = cv2.imread(f'/root/workspace/images/moves/cable1.jpg')
    transform1 = my_class.get_stamped_transform([0.767, 0.495, 0.712], [0.707, 0.001, 0.707, -0.001])
    transform2 = my_class.get_stamped_transform([0.839, 0.493, 0.727], [0.774, 0.001, 0.633, -0.001])

    data1 = my_class.process_image(image1, transform1, show=True)
    data2 = my_class.process_image(image2, transform2, show=True)

    alpha, beta, best_pointcloud_world = my_class.estimate_scale_shift(data1, data2, transform1, transform2, show=True)

def test2():
    my_class = MyClass()
    ros_handler = ROSHandler()

    transform_stamped0 = my_class.get_stamped_transform([0.767, 0.495, 0.712], [0.707, 0.001, 0.707, -0.001])
    transform_stamped6 = my_class.get_stamped_transform([1.025, 0.493, 0.643], [0.983, 0.001, 0.184, -0.000])

    path = ros_handler.interpolate_poses(transform_stamped0, transform_stamped6, num_steps=10)

    while not rospy.is_shutdown():
        ros_handler.publish_path(path, "/my_path")
        rospy.sleep(1)

def test3():
    my_class = MyClass()
    ros_handler = ROSHandler()

    desired_pose = my_class.get_desired_pose([1, 1, 1])
    while not rospy.is_shutdown():
        ros_handler.publish_pose(desired_pose, "/desired_pose")
        rospy.sleep(1)

def pipeline():
    my_class = MyClass()
    ros_handler = ROSHandler()
    image_subscriber = ImageSubscriber('/hsrb/hand_camera/image_rect_color')

    images = []
    transforms = []

    # Take image
    images.append(image_subscriber.get_current_image())
    # Get current transform
    transforms.append(ros_handler.get_stamped_pose("hand_camera_frame", "map"))
    # Process image
    my_class.process_image(images[-1], transforms[-1], show=True)

    # Move arm
    

    # Loop
    # Take image
    # Process image
    # Estimate scale and shift
    # Get desired Pose
    # Calculate Path
    # Move arm a step
    # Loop End


if __name__ == "__main__":
    rospy.init_node("MyClass", anonymous=True)
    pipeline()





