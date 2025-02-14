"""
python celltk/apply.py -i c0/img_00000000* -l c2/img_00000000*

TODO:
need to deal with parent id
"""

from utils.util import imread
import argparse
import tifffile as tiff
from os.path import basename, join, dirname, abspath
import numpy as np
from utils.postprocess_utils import regionprops, Cell # set default parent and next as None
try:
    from labeledarray import LabeledArray
except:
    from celltk.labeledarray import LabeledArray
from os.path import exists
from utils.file_io import make_dirs, lbread
import pandas as pd
import logging
from scipy.ndimage.morphology import binary_fill_holes
from scipy.ndimage.morphology import binary_dilation
import warnings
warnings.filterwarnings('ignore', category=np.VisibleDeprecationWarning)
warnings.filterwarnings('ignore', category=pd.io.pytables.PerformanceWarning)
from _setting import PROP_SAVE, MAX_NUMCELL, FILE_NAME

# We should be able to provide this as configuration when booting CellTK 
#PROP_SAVE = ['area', 'cell_id','max_intensity','mean_intensity', 'median_intensity','total_intensity', 'x', 'y'] #'convex_area', 'cv_intensity',
#             #'eccentricity', 'major_axis_length', 'minor_axis_length', ,
#             #, 'min_intensity', 'orientation',
#            # 'perimeter', 'solidity', 'std_intensity', 'total_intensity', 'x', 'y', #'parent', #'num_seg']

logger = logging.getLogger(__name__)


def find_all_children(labels):
    mask = binary_fill_holes(labels < 0)
    mask[labels < 0] = False
    clabelnums = np.unique(labels[mask]).tolist()
    if 0 in clabelnums:
        clabelnums.remove(0)
    return clabelnums


def find_parent_label(labels, child_label):
    mask = binary_dilation(labels == child_label)
    mask[labels == child_label] = False
    return max(set(labels[mask].tolist()), key=labels[mask].tolist().count)


def add_parent(cells, labels):
    children_labels = find_all_children(labels)
    for cl in children_labels:
        parent_label = find_parent_label(labels, cl)
        child = [cell for cell in cells if cell.label == cl]
        assert len(child) == 1
        child[0].parent = abs(parent_label)
    return cells


def apply():
    pass


def initialize_arr(store):
    nframe = len(store)
    cell_ids = [[i.cell_id for i in cells] for cells in store]
    cell_ids = [i for j in cell_ids for i in j]
    ncells = len(np.unique(cell_ids))
    return np.zeros((len(PROP_SAVE), ncells, nframe)), np.unique(cell_ids).tolist()


def df2larr(df):
    index = df.index.tolist()
    frames = len(np.unique([i[-1] for i in index]))
    index_without_frame = list(set([i[:-1] for i in index]))
    arr = np.empty((df.shape[0]/frames, df.shape[1], frames))
    arr[:] = np.nan
    arr_labels = []
    for num, ind in enumerate(index_without_frame):
        arr_labels.append(ind)
        arr[num, :, :] = df.ix[ind].values.T
    larr = LabeledArray(arr, arr_labels)
    larr.time = np.arange(arr.shape[-1])
    return larr


def _cells2array(cells):
    arr = np.zeros((len(cells), len(PROP_SAVE)), np.float32)
    for cnum, cell in enumerate(cells):
        arr[cnum, :] = [getattr(cell, k) for k in PROP_SAVE]
    return arr


# def multi_index(cells, obj_name, ch_name):
#     frames = np.unique([i.frame for i in cells])
#     index = pd.MultiIndex.from_product([obj_name, ch_name, PROP_SAVE, frames], names=['object', 'ch', 'prop', 'frame'])
#     column_idx = pd.MultiIndex.from_product([np.unique([i.cell_id for i in cells])])
#     df = pd.DataFrame(index=index, columns=column_idx, dtype=np.float32)
#     for cell in cells:
#         for k in PROP_SAVE:
#             df[cell.cell_id].loc[obj_name, ch_name, k, cell.frame] = np.float32(getattr(cell, k))
#     return df


def caller(inputs_list, inputs_labels_list, output, primary, secondary):
    make_dirs(dirname(abspath(output)))

    inputs_list = [inputs_list, ] if isinstance(inputs_list[0], str) else inputs_list
    inputs_labels_list = [inputs_labels_list, ] if isinstance(inputs_labels_list[0], str) else inputs_labels_list

    obj_names = [basename(dirname(i[0])) for i in inputs_labels_list] if primary is None else primary
    ch_names = [basename(dirname(i[0])) for i in inputs_list] if secondary is None else secondary

    for inputs, ch in zip(inputs_list, ch_names):
        for inputs_labels, obj in zip(inputs_labels_list, obj_names):
            logger.info("Channel {0}: {1} applied...".format(ch, obj))
            arr = np.ones((MAX_NUMCELL, len(PROP_SAVE), len(inputs)), np.float32)  * np.nan
            for frame, (path, pathl) in enumerate(zip(inputs, inputs_labels)):
                img, labels = imread(path), lbread(pathl, nonneg=False)
                cells = regionprops(labels, img)
                if (labels < 0).any():
                    cells = add_parent(cells, labels)
                [setattr(cell, 'frame', frame) for cell in cells]
                cells = [Cell(cell) for cell in cells]
                tarr = _cells2array(cells)
                index = tarr[:, 1].astype(np.int32)
                arr[index, :, frame] = tarr

            logger.info("\tmaking dataframe...")
            cellids = np.where(~np.isnan(arr[:, 0, :]).all(axis=1))[0]
            marr = np.zeros((len(cellids), arr.shape[1], arr.shape[2]))
            for pn, i in enumerate(cellids):
                marr[pn] = arr[i]
            sarr = np.swapaxes(marr, 0, 2)
            narr = sarr.reshape((sarr.shape[0]*sarr.shape[1], sarr.shape[2]), order='F')
            index = pd.MultiIndex.from_product([obj, ch, PROP_SAVE, range(arr.shape[-1])], names=['object', 'ch', 'prop', 'frame'])
            df = pd.DataFrame(narr, index=index, columns=cellids)

            if exists(join(output, FILE_NAME+'.csv')):
                ex_df = pd.read_csv(join(output, FILE_NAME+'.csv'), index_col=['object', 'ch', 'prop', 'frame'])
                ex_df.columns = pd.to_numeric(ex_df.columns)
                ex_df = ex_df.astype(np.float32)
                df = pd.concat([df, ex_df])
            df.to_csv(join(output, FILE_NAME+'.csv'))
    larr = df2larr(df)
    larr.save(join(output, FILE_NAME+'.npz'))
    logger.info("\t" + FILE_NAME + ".npz saved.")


def main():
    parser = argparse.ArgumentParser()
    parser.add_argument("-i", "--inputs", help="images", nargs="*", default=[])
    parser.add_argument("-l", "--labels", help="labels", nargs="+")
    parser.add_argument("-o", "--output", help="file name", type=str, default='temp')
    parser.add_argument("-p", "--primary", help="name for primary key, typically objects", type=str, default=None)
    parser.add_argument("-s", "--secondary", help="name for secondary key, typically channels", type=str, default=None)
    args = parser.parse_args()

    output = args.output

    caller(args.inputs, args.labels, args.output, args.primary, args.secondary)


if __name__ == "__main__":
    logging.basicConfig(level=logging.INFO)
    main()
