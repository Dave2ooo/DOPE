import os
import sys
import cv2
import torch
import numpy as np
from numpy.typing import NDArray
import supervision as sv
from PIL import Image

grounded_sam_directory = '/root/grounded_sam2'
# sys.path.insert(1, grounded_sam_directory)
sys.path.append(grounded_sam_directory)
from sam2.build_sam import build_sam2_video_predictor, build_sam2
from sam2.sam2_image_predictor import SAM2ImagePredictor
from transformers import AutoProcessor, AutoModelForZeroShotObjectDetection 
from utils.track_utils import sample_points_from_masks
from utils.video_utils import create_video_from_images


class GroundedSamWrapper:
    def __init__(self):
        """
        Step 1: Environment settings and model initialization
        """
        # use bfloat16 for the entire notebook
        torch.autocast(device_type="cuda", dtype=torch.bfloat16).__enter__()

        if torch.cuda.get_device_properties(0).major >= 8:
            # turn on tfloat32 for Ampere GPUs (https://pytorch.org/docs/stable/notes/cuda.html#tensorfloat-32-tf32-on-ampere-devices)
            torch.backends.cuda.matmul.allow_tf32 = True
            torch.backends.cudnn.allow_tf32 = True

        # init sam image predictor and video predictor model
        self.sam2_checkpoint = f'{grounded_sam_directory}/checkpoints/sam2_hiera_large.pt'
        self.model_cfg = "sam2_hiera_l.yaml"

        # self.video_predictor = build_sam2_video_predictor(self.model_cfg, self.sam2_checkpoint)
        sam2_image_model = build_sam2(self.model_cfg, self.sam2_checkpoint)
        self.image_predictor = SAM2ImagePredictor(sam2_image_model)

        # init grounding dino model from huggingface
        # model_id = "IDEA-Research/grounding-dino-tiny"
        self.model_id = "IDEA-Research/grounding-dino-base"
        self.device = "cuda" if torch.cuda.is_available() else "cpu"
        self.processor = AutoProcessor.from_pretrained(self.model_id)
        self.grounding_model = AutoModelForZeroShotObjectDetection.from_pretrained(self.model_id).to(self.device)

    def get_mask(self, image: NDArray[np.uint8], prompt="tube.cable."):
        image_pil = Image.fromarray(cv2.cvtColor(image, cv2.COLOR_BGR2RGB))
        text = prompt

        # run Grounding DINO on the image
        inputs = self.processor(images=image_pil, text=text, return_tensors="pt").to(self.device)
        with torch.no_grad():
            outputs = self.grounding_model(**inputs)

        results = self.processor.post_process_grounded_object_detection(
            outputs,
            inputs.input_ids,
            box_threshold=0.25,
            text_threshold=0.3,
            target_sizes=[image_pil.size[::-1]]
        )

        # prompt SAM image predictor to get the mask for the object
        self.image_predictor.set_image(np.array(image_pil.convert("RGB")))

        # process the detection results
        input_boxes = results[0]["boxes"].cpu().numpy()
        OBJECTS = results[0]["labels"]

        # prompt SAM 2 image predictor to get the mask for the object
        masks, scores, logits = self.image_predictor.predict(
            point_coords=None,
            point_labels=None,
            box=input_boxes,
            multimask_output=False,
        )
        # print(f'masks.shape = {masks.shape}')
        # convert the mask shape to (n, H, W)
        if masks.ndim == 3:
            masks = masks[None]
            scores = scores[None]
            logits = logits[None]
        # elif masks.ndim == 4:
            # masks = masks.squeeze(1)

        # print(f'masks.shape = {masks.shape}')
        return masks
    
    def show_mask(self, mask: NDArray[np.ndarray], title="mask", wait = True):
        """
        Display a binary or float mask using OpenCV, with nothing else overlaid.
        Blocks until any key is pressed.
        """
        # if mask is (N, H, W), take the first one
        if mask.ndim == 3:
            mask = mask[0]

        # convert boolean or float mask to uint8 0–255
        mask_uint8 = (mask.astype(np.float32) * 255).astype(np.uint8)

        # create a resizable window (you can switch to WINDOW_AUTOSIZE if you prefer)
        cv2.namedWindow(title, cv2.WINDOW_NORMAL)
        cv2.imshow(title, mask_uint8)
        if wait:
            cv2.waitKey(0)

    def show_masks(self, masks, title="masks", wait=True) -> None:
        """
        Display multiple binary or float masks in one window, each colored differently.
        Blocks until any key is pressed.

        Parameters
        ----------
        masks : np.ndarray or sequence of np.ndarray
            If a single 2D mask (H×W), it will be wrapped in a list.
            If an array of shape (N, H, W), each slice along axis 0 is treated as one mask.
            Or you can pass a list/tuple of 2D masks.
        """
        import cv2
        import numpy as np

        # Normalize input to a list of 2D masks
        if isinstance(masks, np.ndarray):
            if masks.ndim == 2:
                mask_list = [masks]
            elif masks.ndim == 3:
                mask_list = [masks[i] for i in range(masks.shape[0])]
            else:
                raise ValueError("masks array must have 2 or 3 dimensions")
        elif isinstance(masks, (list, tuple)):
            mask_list = list(masks)
        else:
            raise TypeError("masks must be a numpy array or a list/tuple of arrays")

        # All masks must have the same shape
        H, W = mask_list[0].shape

        # Pre‐defined distinct BGR colors
        colors = [
            (0, 0, 255),    # red
            (0, 255, 0),    # green
            (255, 0, 0),    # blue
            (0, 255, 255),  # yellow
            (255, 0, 255),  # magenta
            (255, 255, 0),  # cyan
            (128, 0, 128),  # purple
            (0, 128, 128),  # teal
            (128, 128, 0),  # olive
        ]

        # Create an empty color canvas
        canvas = np.zeros((H, W, 3), dtype=np.uint8)

        # Overlay each mask in its color
        for idx, mask in enumerate(mask_list):
            # If mask has extra dims (e.g. (1, H, W)), take the first channel
            if mask.ndim == 3:
                mask = mask[0]
            mask_resized = cv2.resize(mask, (W, H), interpolation=cv2.INTER_NEAREST)
            mask_bool = mask_resized.astype(bool)
            color = colors[idx % len(colors)]
            canvas[mask_bool] = color

        # Display the combined masks
        cv2.namedWindow(title, cv2.WINDOW_NORMAL)
        cv2.imshow(title, canvas)
        if wait:
            cv2.waitKey(0)

    def show_mask_and_points(self, mask: np.ndarray, points, title = "mask_with_points") -> None:
        """
        Display a single mask with overlaid points using OpenCV.

        Parameters
        ----------
        mask : np.ndarray
            2D mask array (H×W), dtype float in [0,1] or uint8 in [0,255].
        points : sequence of (x, y)
            List or array of pixel coordinates to draw on the mask.
        """
        import cv2
        import numpy as np

        # Normalize mask to uint8 [0,255]
        if mask.dtype == np.float32 or mask.dtype == np.float64:
            mask_img = (mask * 255).astype(np.uint8)
        else:
            mask_img = mask.copy().astype(np.uint8)

        # Convert to BGR so we can draw colored points
        canvas = cv2.cvtColor(mask_img, cv2.COLOR_GRAY2BGR)

        # Draw each point as a small circle (red)
        for pt in points:
            x, y = int(round(pt[0])), int(round(pt[1]))
            cv2.circle(canvas, (x, y), radius=3, color=(0, 0, 255), thickness=-1)

        # Show result
        cv2.namedWindow(title, cv2.WINDOW_NORMAL)
        cv2.imshow(title, canvas)
        cv2.waitKey(0)
        cv2.destroyAllWindows()

    def get_closest_point(self,
                          mask: np.ndarray,
                          point):
        """
        Find the nearest non-zero pixel in a binary mask to a given point.

        Parameters
        ----------
        mask : np.ndarray
            2D array, dtype float or uint8, where non-zero values are valid mask points.
        point : tuple of two floats
            The (x, y) coordinate to search from.

        Returns
        -------
        (x_closest, y_closest) : tuple of int
            The pixel coordinate in the mask closest to `point`.
        """
        import numpy as np

        # Build boolean mask of valid points
        mask_bool = mask.astype(bool)
        # Extract (row, col) indices of all true pixels
        coords = np.column_stack(np.nonzero(mask_bool))  # shape (N,2): (y, x)
        if coords.size == 0:
            raise ValueError("Mask contains no foreground pixels")

        # Round input point and split into ints
        x0, y0 = point
        x0, y0 = float(x0), float(y0)
        # We'll compare to (row=y, col=x)
        p = np.array([y0, x0], dtype=np.float64)

        # Compute squared distances and find the index of the minimum
        deltas = coords.astype(np.float64) - p
        d2 = np.einsum('ij,ij->i', deltas, deltas)
        idx = np.argmin(d2)

        y_closest, x_closest = coords[idx]
        return int(x_closest), int(y_closest)
 
    def mask_depth_map(self,
                       depth: np.ndarray,
                       mask: np.ndarray) -> np.ndarray:
        """
        Apply a 2D mask to a depth map, zeroing out all pixels outside the mask.

        Parameters
        ----------
        depth : np.ndarray
            Input depth map of shape (H, W), dtype float or int.
        mask : np.ndarray
            Binary or boolean mask of shape (H, W). Non-zero/True means keep.

        Returns
        -------
        np.ndarray
            A new depth map where depth[i, j] is preserved if mask[i, j] else 0.
        """
        if not isinstance(depth, np.ndarray):
            raise TypeError("depth must be a numpy.ndarray")
        if not isinstance(mask, np.ndarray):
            raise TypeError("mask must be a numpy.ndarray")
        if depth.shape != mask.shape:
            raise ValueError("depth and mask must have the same shape")

        # make boolean mask
        mask_bool = mask.astype(bool)

        # apply mask
        result = depth.copy()
        result[~mask_bool] = 0
        return result



if __name__ == "__main__":
    image = cv2.imread('/root/workspace/images/moves/cable0.jpg')

    grounded_sam_wrapper = GroundedSamWrapper()
    masks = grounded_sam_wrapper.get_mask(image)
    grounded_sam_wrapper.show_mask(masks[0])