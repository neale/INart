import os
import glob
import shutil
import logging
import warnings

import torch
import tifffile
import numpy as np
import networkx
import matplotlib.pyplot as plt
from scipy.interpolate import splev, splprep


logging.getLogger().setLevel(logging.ERROR)


@torch.no_grad()
def lerp(z1, z2, n):
    delta = (z2['sample'] - z1['sample']) / (n + 1)
    total_frames = n + 2
    states = []
    for i in range(total_frames):
        z = z1['sample'] + delta * float(i)
        zx = {'sample': z, 'sample_shape': z1['sample_shape']}
        states.append(zx)
        z = None
    return states


@torch.no_grad()
def slerp(z1, z2, n):
    # Ensure the points are normalized to lie on the unit sphere
    z1_norm = z1['sample'] / torch.norm(z1['sample'], p=2, dim=-1, keepdim=True)
    z2_norm = z2['sample'] / torch.norm(z2['sample'], p=2, dim=-1, keepdim=True)
    # Calculate the angle between the points
    dot = torch.clamp(torch.sum(z1_norm * z2_norm, dim=1), -1.0, 1.0)
    theta = torch.acos(dot)  # angle between input vectors
    # Create an array of angles from 0 to 2*pi with n points
    angles = torch.linspace(0, 2 * np.pi, n+1)[:-1]  # remove the last value to prevent duplicating the first point
    # Use SLERP formula to interpolate
    sin_theta = torch.sin(theta)
    slerp_points = []
    for angle in angles:
        alpha = torch.sin((1.0 - angle/theta) * theta) / sin_theta
        beta = torch.sin(angle/theta * theta) / sin_theta
        slerp_point = alpha * z1_norm + beta * z2_norm
        zx = {'sample': slerp_point, 'sample_shape': z1['sample_shape']}
        slerp_points.append(zx)
    return slerp_points


@torch.no_grad()
def rspline(z_points, n, degree=3, device='cpu'):
    # Ensure the spline is closed by duplicating the first point at the end
    if device != 'cpu':
        control_points = [z['sample'].detach().to('cpu').numpy() for z in z_points]
    else:
        control_points = [z['sample'].numpy() for z in z_points]
    num_control_points = len(control_points)
    closed_points = np.stack(control_points)
    closed_points = np.concatenate([closed_points, control_points[0][None, ...]])
    original_ndim = closed_points.ndim
    if closed_points.ndim == 4 and closed_points.shape[1] == 1 and closed_points.shape[2] == 1:
        closed_points = closed_points.squeeze(1).squeeze(1)
    # Use scipy's splprep to create a tck representation of the spline, with a periodic condition
    tck, _ = splprep(closed_points.T, per=True, k=min(degree, num_control_points-1))

    # Sample n points along the spline
    u_new = np.linspace(0, 1, n)
    points = np.array(splev(u_new, tck, der=0)).T
    plt.ion()
    plt.clf()
    plt.show()
    plt.plot(points[:, 0], points[:, 1], color='blue')
    plt.scatter(points[:, 0], points[:, 1], color='purple')
    plt.draw()
    plt.pause(0.5)
    
    if original_ndim == 4:
        points = points[:, None, None, :]
    states = []
    for point in points:
        zx = {'sample': torch.from_numpy(point).float().to(device), 'sample_shape': z_points[0]['sample_shape']}
        states.append(zx)
    return states


@torch.no_grad()
def lemniscate(z1, z2, n, a=1):
    t_values = torch.linspace(0, 2 * torch.pi, n + 2)
    x = a * torch.cos(t_values) / (1 + torch.sin(t_values)**2)
    y = a * torch.cos(t_values) * torch.sin(t_values) / (1 + torch.sin(t_values)**2)
    
    # Normalize x, y to range between the samples in z1 and z2
    x = x - x.min()
    x = x / x.max()
    y = y - y.min()
    y = y / y.max()
    
    # Scale and shift to actual sample values
    delta_x = z2['sample'] - z1['sample']
    x_scaled = z1['sample'] + delta_x * x
    y_scaled = z1['sample'] + delta_x * y

    # Prepare the states array
    states = []
    for i in range(n + 2):
        z = x_scaled[i] + y_scaled[i] * 1j  # Combining x and y components
        zx = {'sample': z, 'sample_shape': z1['sample_shape']}
        states.append(zx)
    
    return states


def load_image_as_tensor(path, output_dir='/tmp', device='cpu'):
    import cv2
    target = cv2.cvtColor(cv2.imread(path), cv2.COLOR_BGR2RGB)
    target = target / 127.5 - 1 
    target = torch.from_numpy(target).permute(2, 0, 1).unsqueeze(0).float().to(device)
    target_fn = f'{output_dir}/target'
    if not os.path.isdir(output_dir):
        os.makedirs(output_dir, exist_ok=True)
    write_image(path=target_fn,
        img=(target.permute(0, 2, 3, 1)[0].cpu().numpy()+1)*127.5, suffix='jpg')
    return target


def unnormalize_and_numpy(x):
    x = x.detach().cpu().numpy()
    x = (x - x.min()) / (x.ptp() + 1e-10)
    x += (np.random.random(x.shape) - 0.5) * (5.0 / 256)
    x = np.clip(x, 0, 1)
    x = (x * 255.).astype(np.uint8)
    return x


def write_image(path, img, suffix='jpg', metadata=None, colormaps=None):
    import cv2
    assert suffix in ['jpg', 'png', 'bmp', 'jpeg', 'tif'], f'Invalid suffix for file, got {suffix}'
    if suffix in ['jpg', 'png', 'bmp', 'jpeg']:
        if colormaps is not None:
            colormapped_imgs = image_colormaps(img, colormaps)
            for cmap, cmap_img in colormapped_imgs.items():
                cmap_path = path + f'_{cmap}.{suffix}'
                cv2.imwrite(cmap_path, cmap_img)
                assert os.path.isfile(cmap_path)
        else:
            path = path + f'.{suffix}'
            cv2.imwrite(path, img)
            assert os.path.isfile(path)
    elif suffix == 'tif':
        if metadata is None:
            warnings.warn('No metadata provided for tiff file, data will not be reproducible.')
        path = path + '.tif'
        tifffile.imwrite(path, img, metadata=metadata)
    else:
        raise NotImplementedError


def image_colormaps(img, colormaps):
    import cv2
    colormaps = {c.lower(): None for c in colormaps}
    if img.shape[-1] == 3:
        colormaps['rgb'] = img
        if 'hsv' in colormaps:
            colormaps['hsv'] = cv2.cvtColor(img, cv2.COLOR_RGB2HSV)
        if 'gray' in colormaps:
            colormaps['gray'] = cv2.cvtColor(img, cv2.COLOR_RGB2GRAY)
        if 'lab' in colormaps:
            colormaps['lab'] = cv2.cvtColor(img, cv2.COLOR_RGB2LAB)
        if 'hls' in colormaps:
            colormaps['hls'] = cv2.cvtColor(img, cv2.COLOR_RGB2HLS)
        if 'luv' in colormaps:
            colormaps['luv'] = cv2.cvtColor(img, cv2.COLOR_RGB2LUV)
    else:
        img2 = cv2.cvtColor(img, cv2.COLOR_GRAY2RGB)
        colormaps['gray'] = img
        if 'rgb' in colormaps:
            colormaps['rgb'] = img2
        if 'hsv' in colormaps:
            colormaps['hsv'] = cv2.cvtColor(img2, cv2.COLOR_RGB2HSV)
        if 'lab' in colormaps:
            colormaps['lab'] = cv2.cvtColor(img2, cv2.COLOR_RGB2LAB)
        if 'hls' in colormaps:
            colormaps['hls'] = cv2.cvtColor(img2, cv2.COLOR_RGB2HLS)
        if 'luv' in colormaps:
            colormaps['luv'] = cv2.cvtColor(img2, cv2.COLOR_RGB2LUV) 
    return colormaps


def save_repository(search_dir, output_dir):
    assert os.path.isdir(output_dir), f'{output_dir} not found'
    assert os.path.isdir(search_dir), f'{search_dir} not found'
    py_files = glob.glob('*.py')
    assert len(py_files) > 0
    for fn in py_files:
        shutil.copy(fn, os.path.join(output_dir, fn))


def load_tif_metadata(path):
    assert os.path.isfile(path), f'{path} not found'
    try:
        with tifffile.TiffFile(path) as tif:
            img = tif.asarray()
            data = tif.shaped_metadata[0]
    except:
        warnings.warn(f'Could not load metadata from {path}')
        return None, None
    metadata = {  # these are the keys that are always present
        'seed': int(data['seed']),
        'latent_dim': int(data['latent_dim']),
        'latent_scale': float(data['latent_scale']),
        'x_dim': int(data['x_dim']),
        'y_dim': int(data['y_dim']),
        'c_dim': int(data['c_dim']),
        'device': data['device'],
    }
    if 'z_dim' in data:
        metadata['z_dim'] = int(data['z_dim'])
    else:
        metadata['z_dim'] = int(data['x_dim'])
    for int_key in ['mlp_layer_width', 'conv_feature_map_size', 'input_encoding_dim', 'num_graph_nodes']:
        try:
            metadata[int_key] = int(data[int_key])
        except KeyError:
            metadata[int_key] = None
            warnings.warn(f'Key {int_key} not found in metadata, setting to None.')
    for float_key in ['weight_init_mean', 'weight_init_std', 'weight_init_max', 'weight_init_min']:
        try:
            metadata[float_key] = float(data[float_key])
        except KeyError:
            metadata[float_key] = None
            warnings.warn(f'Key {float_key} not found in metadata, setting to None.')
    for str_key in ['activations', 'graph', 'final_activation', 'weight_init', 'graph_topology']:
        try:
            metadata[str_key] = data[str_key]
        except KeyError:
            metadata[str_key] = None
            warnings.warn(f'Key {str_key} not found in metadata, setting to None.')
    for tensor_key in ['latents']:
        try:
            metadata[tensor_key] = torch.Tensor(data[tensor_key])
        except KeyError:
            if 'latent' in data:
                metadata['latents'] = torch.tensor(data['latent'])
                warnings.warn(f'Key {tensor_key} not found in metadata, but found `latent` (old style).. setting.')
            else:
                metadata[tensor_key] = None
                warnings.warn(f'Key {tensor_key} not found in metadata, setting to None.')
        
    if metadata['input_encoding_dim'] is None:
        metadata['input_encoding_dim'] = 1
    if metadata['activations'] == 'basic':
        metadata['activations'] = 'fixed'
    return img, metadata


def draw_graph(num_nodes, random_graph, graph, c_dim=3, img=None):
    import cv2
    graph.dpi = 1000
    options = {
        'label': '',
        "font_size": 36,
        "node_size": 3000,
        "node_color": "white",
        "edgecolors": "black",
        "linewidths": 3,
        "width": 2,
        "with_labels": False,
    }
    if random_graph:
        if num_nodes > 40:
            plot_size = 30
        elif num_nodes > 20:
            plot_size = 90
        elif num_nodes > 10:
            plot_size = 200
        else:
            plot_size = 250
        options['node_size'] = plot_size

    H_layout = networkx.nx_pydot.pydot_layout(graph, prog='dot')
    networkx.draw_networkx(graph, H_layout, **options)
    ax = plt.gca()
    ax.margins(0.20)
    plt.axis("off")
    plt.savefig('temp_net.png', dpi=700)
    x = cv2.imread('temp_net.png')

    if c_dim == 3:
        x = cv2.cvtColor(x, cv2.COLOR_BGR2RGB)
    else:
        x = cv2.cvtColor(x, cv2.COLOR_BGR2GRAY)
    x = cv2.bitwise_not(x)
    x_s = cv2.resize(x, (100, 100), interpolation=cv2.INTER_AREA)
    if c_dim == 1:
        x_s = x_s.reshape((x_s.shape[0], x_s.shape[1], 1))
    img_trans = np.zeros_like(img)
    img_trans[-x_s.shape[0]:, -x_s.shape[1]:, :] = x_s
    plt.close('all')
    return img_trans


def write_video(frames, save_path, ffmpeg=False):
    if ffmpeg:
        save_dir = '/'.join(save_path.split('/')[:-1])
        print (save_dir)
        print(save_path)
        os.system(f'ffmpeg -framerate 10 -i {save_dir}/frame_%05d.png -c:v libx264 -pix_fmt yuv420p {save_path}.mp4')
    else:
        import cv2
        fourcc = cv2.VideoWriter_fourcc(*'mp4v') 
        width = frames[0].shape[0]
        height = frames[0].shape[1]
        video = cv2.VideoWriter(f'{save_path}.mp4', fourcc, 10., (width, height))
        for frame in frames: 
            video.write(frame)
        cv2.destroyAllWindows()
        video.release()