# -*- coding: utf-8 -*-

"""Plotting features."""


#------------------------------------------------------------------------------
# Imports
#------------------------------------------------------------------------------

import numpy as np

from vispy import gloo

from ._vispy_utils import (BaseSpikeVisual,
                           BaseSpikeCanvas,
                           BoxVisual,
                           AxisVisual,
                           LassoVisual,
                           _enable_depth_mask,
                           _wrap_vispy,
                           )
from ._panzoom import PanZoomGrid
from ..ext.six import string_types
from ..utils._types import _as_array, _is_integer
from ..utils.array import _index_of, _unique
from ..utils._color import _selected_clusters_colors


#------------------------------------------------------------------------------
# Features visual
#------------------------------------------------------------------------------

def _alternative_dimension(dim, n_features=None, n_channels=None):
    assert n_features >= 1
    assert n_channels >= 1
    if dim == 'time':
        return (0, 0)
    else:
        channel, fet = dim
        if n_features >= 2:
            return (channel, (fet + 1) % n_features)
        elif n_channels >= 2:
            return ((channel + 1) % n_channels, fet)
        else:
            return 'time'


def _matrix_from_dimensions(dimensions, n_features=None, n_channels=None):
    n = len(dimensions)
    matrix = np.empty((n, n), dtype=object)
    for i in range(n):
        for j in range(n):
            dim_x, dim_y = dimensions[i], dimensions[j]
            if dim_x == dim_y:
                dim_y = _alternative_dimension(dim_x,
                                               n_features=n_features,
                                               n_channels=n_channels,
                                               )
                # For aesthetical reasons, put time on the x axis if it is
                # the alternative dimension.
                if dim_y == 'time':
                    dim_x, dim_y = dim_y, dim_x
            matrix[i, j] = (dim_x, dim_y)
    return matrix


class BaseFeatureVisual(BaseSpikeVisual):
    """Display a grid of multidimensional features."""

    _shader_name = None
    _gl_draw_mode = 'points'

    def __init__(self, **kwargs):
        super(BaseFeatureVisual, self).__init__(**kwargs)

        self._features = None
        self._spike_samples = None
        self._dimensions_matrix = np.empty((0, 0), dtype=object)
        self.n_channels, self.n_features = None, None
        self.n_rows = None

        _enable_depth_mask()

    # Data properties
    # -------------------------------------------------------------------------

    @property
    def spike_samples(self):
        """Time samples of the displayed spikes."""
        return self._spike_samples

    @spike_samples.setter
    def spike_samples(self, value):
        assert isinstance(value, np.ndarray)
        assert value.shape == (self.n_spikes,)
        self._spike_samples = value

    @property
    def features(self):
        """Displayed features.

        This is a `(n_spikes, n_features)` array.

        """
        return self._features

    @features.setter
    def features(self, value):
        self._set_features_to_bake(value)

    def _set_features_to_bake(self, value):
        # WARNING: when setting new data, features need to be set first.
        # n_spikes will be set as a function of features.
        value = _as_array(value)
        # TODO: support sparse structures
        assert value.ndim == 3
        self.n_spikes, self.n_channels, self.n_features = value.shape
        self._features = value
        self._empty = self.n_spikes == 0
        self.set_to_bake('spikes',)

    def _check_dimension(self, dim):
        if _is_integer(dim):
            dim = (dim, 0)
        if isinstance(dim, tuple):
            assert len(dim) == 2
            channel, feature = dim
            assert _is_integer(channel)
            assert _is_integer(feature)
            assert 0 <= channel < self.n_channels
            assert 0 <= feature < self.n_features
        elif isinstance(dim, string_types):
            assert dim == 'time'
        else:
            raise ValueError('{0} should be (channel, feature) '.format(dim) +
                             'or "time".')

    def _get_feature_dim(self, data, dim):
        if isinstance(dim, (tuple, list)):
            channel, feature = dim
            return data[:, channel, feature]
        elif dim == 'time':
            t = self._spike_samples
            # Default times.
            if t is None:
                t = np.arange(self.n_spikes)
            # Normalize time feature.
            m = float(t.max())
            if m > 0:
                t = (-1. + 2 * t / m) * .8
            return t

    def project(self, data, box):
        """Project data to a subplot's two-dimensional subspace.

        Parameters
        ----------
        data : array
            The shape is `(n_points, n_channels, n_features)`.
        box : 2-tuple
            The `(row, col)` of the box.

        Notes
        -----

        The coordinate system is always the world coordinate system, i.e.
        `[-1, 1]`.

        """
        i, j = box
        dim_x, dim_y = self._dimensions_matrix[i, j]

        fet_x = self._get_feature_dim(self._features, dim_x)
        fet_y = self._get_feature_dim(self._features, dim_y)

        # NOTE: we switch here because we want to plot
        # dim_x (y) over dim_y (x) on box (i, j).
        return np.c_[fet_x, fet_y]

    @property
    def dimensions_matrix(self):
        """Displayed dimensions matrix.

        This is a matrix of pairs of items which can be:

        * tuple `(channel_id, feature_idx)`
        * `'time'`

        """
        return self._dimensions_matrix

    @dimensions_matrix.setter
    def dimensions_matrix(self, value):
        self._set_dimensions_to_bake(value)

    def _set_dimensions_to_bake(self, value):
        if not isinstance(value, np.ndarray):
            value = np.array(value, dtype=object)
        assert value.ndim == 2
        assert value.shape[0] == value.shape[1]
        assert value.dtype == object
        self.n_rows = len(value)
        for (dim_x, dim_y) in value.flat:
            self._check_dimension(dim_x)
            self._check_dimension(dim_y)
        self._dimensions_matrix = value
        self.set_to_bake('spikes',)

    @property
    def n_boxes(self):
        """Number of boxes in the grid."""
        return self.n_rows * self.n_rows

    # Data baking
    # -------------------------------------------------------------------------

    def _bake_spikes(self):
        n_points = self.n_boxes * self.n_spikes

        # index increases from top to bottom, left to right
        # same as matrix indices (i, j) starting at 0
        positions = []
        boxes = []

        for i in range(self.n_rows):
            for j in range(self.n_rows):
                pos = self.project(self._features, (i, j))
                positions.append(pos)
                index = self.n_rows * i + j
                boxes.append(index * np.ones(self.n_spikes, dtype=np.float32))

        positions = np.vstack(positions).astype(np.float32)
        boxes = np.hstack(boxes)

        assert positions.shape == (n_points, 2)
        assert boxes.shape == (n_points,)

        self.program['a_position'] = positions.copy()
        self.program['a_box'] = boxes
        self.program['n_rows'] = self.n_rows


class BackgroundFeatureVisual(BaseFeatureVisual):
    """Display a grid of multidimensional features in the background."""

    _shader_name = 'features_bg'
    _transparency = False


class FeatureVisual(BaseFeatureVisual):
    """Display a grid of multidimensional features."""

    _shader_name = 'features'

    def __init__(self, **kwargs):
        super(FeatureVisual, self).__init__(**kwargs)
        self.program['u_size'] = 3.

    # Data properties
    # -------------------------------------------------------------------------

    def _set_features_to_bake(self, value):
        super(FeatureVisual, self)._set_features_to_bake(value)
        self.set_to_bake('spikes', 'spikes_clusters', 'color')

    def _get_mask_dim(self, dim):
        if isinstance(dim, (tuple, list)):
            channel, feature = dim
            return self._masks[:, channel]
        elif dim == 'time':
            return np.ones(self.n_spikes)

    def _set_dimensions_to_bake(self, value):
        super(FeatureVisual, self)._set_dimensions_to_bake(value)
        self.set_to_bake('spikes', 'spikes_clusters', 'color')

    # Data baking
    # -------------------------------------------------------------------------

    def _bake_spikes(self):
        n_points = self.n_boxes * self.n_spikes

        # index increases from top to bottom, left to right
        # same as matrix indices (i, j) starting at 0
        positions = []
        masks = []
        boxes = []

        for i in range(self.n_rows):
            for j in range(self.n_rows):

                pos = self.project(self._features, (i, j))
                positions.append(pos)

                # The mask depends on the `y` coordinate.
                dim = self._dimensions_matrix[i, j][1]
                mask = self._get_mask_dim(dim)
                masks.append(mask.astype(np.float32))

                index = self.n_rows * i + j
                boxes.append(index * np.ones(self.n_spikes, dtype=np.float32))

        positions = np.vstack(positions).astype(np.float32)
        masks = np.hstack(masks)
        boxes = np.hstack(boxes)

        assert positions.shape == (n_points, 2)
        assert masks.shape == (n_points,)
        assert boxes.shape == (n_points,)

        self.program['a_position'] = positions.copy()
        self.program['a_mask'] = masks
        self.program['a_box'] = boxes

        self.program['n_clusters'] = self.n_clusters
        self.program['n_rows'] = self.n_rows

    def _bake_spikes_clusters(self):
        # Get the spike cluster indices (between 0 and n_clusters-1).
        spike_clusters_idx = self.spike_clusters
        # We take the cluster order into account here.
        spike_clusters_idx = _index_of(spike_clusters_idx, self.cluster_order)
        a_cluster = np.tile(spike_clusters_idx,
                            self.n_boxes).astype(np.float32)
        self.program['a_cluster'] = a_cluster
        self.program['n_clusters'] = self.n_clusters

    @property
    def marker_size(self):
        """Marker size in pixels."""
        return float(self.program['u_size'])

    @marker_size.setter
    def marker_size(self, value):
        value = np.clip(value, .1, 100)
        self.program['u_size'] = float(value)
        self.update()


class FeatureView(BaseSpikeCanvas):
    """A VisPy canvas displaying features."""
    _visual_class = FeatureVisual
    _events = ('enlarge',)

    def _create_visuals(self):
        self.boxes = BoxVisual()
        self.axes = AxisVisual()
        self.background = BackgroundFeatureVisual()
        self.lasso = LassoVisual()
        super(FeatureView, self)._create_visuals()

    def _create_pan_zoom(self):
        self._pz = PanZoomGrid()
        self._pz.add(self.visual.program)
        self._pz.add(self.background.program)
        self._pz.add(self.lasso.program)
        self._pz.add(self.axes.program)
        self._pz.aspect = None
        self._pz.attach(self)

    def _set_pan_constraints(self, matrix):
        n = len(matrix)
        xmin = np.empty((n, n))
        xmax = np.empty((n, n))
        ymin = np.empty((n, n))
        ymax = np.empty((n, n))
        gpza = np.empty((n, n), dtype=np.str)
        gpza.fill('b')
        for arr in (xmin, xmax, ymin, ymax):
            arr.fill(np.nan)
        _index_set = False
        for i in range(n):
            for j in range(n):
                dim_x, dim_y = matrix[i, j]
                if dim_x == 'time':
                    xmin[i, j] = -1.
                    xmax[i, j] = +1.
                    gpza[i, j] = 'x'
                if dim_y == 'time':
                    ymin[i, j] = -1.
                    ymax[i, j] = +1.
                    gpza[i, j] = 'y' if gpza[i, j] != 'x' else 'n'
                else:
                    # Set the current index to the first non-time axis.
                    if not _index_set:
                        self._pz._index = (i, i)
                    _index_set = True
        self._pz._xmin = xmin
        self._pz._xmax = xmax
        self._pz._ymin = ymin
        self._pz._ymax = ymax
        self._pz.global_pan_zoom_axis = gpza

    def set_data(self,
                 features=None,
                 dimensions=None,
                 masks=None,
                 spike_clusters=None,
                 spike_samples=None,
                 background_features=None,
                 colors=None,
                 ):
        if features is not None:
            assert isinstance(features, np.ndarray)
            if features.ndim == 2:
                features = features[..., None]
            assert features.ndim == 3
        else:
            features = self.visual.features
        n_spikes, n_channels, n_features = features.shape

        if spike_clusters is None:
            spike_clusters = np.zeros(n_spikes, dtype=np.int32)
        cluster_ids = _unique(spike_clusters)
        n_clusters = len(cluster_ids)

        if masks is None:
            masks = np.ones(features.shape[:2], dtype=np.float32)

        if dimensions is None:
            dimensions = [(0, 0)]

        if colors is None:
            colors = _selected_clusters_colors(n_clusters)

        self.visual.features = features

        if background_features is not None:
            assert features.shape[1:] == background_features.shape[1:]
            self.background.features = background_features.astype(np.float32)
            if spike_samples is not None:
                assert spike_samples.shape == (n_spikes,)
                self.background.spike_samples = spike_samples

        if masks is not None:
            self.visual.masks = masks

        if not len(self.dimensions_matrix):
            matrix = _matrix_from_dimensions(dimensions,
                                             n_features=n_features,
                                             n_channels=n_channels,
                                             )
            self.dimensions_matrix = matrix

        self.visual.spike_clusters = spike_clusters
        assert spike_clusters.shape == (n_spikes,)
        if spike_samples is not None:
            self.visual.spike_samples = spike_samples

        self.visual.cluster_colors = colors

        self.update()

    @property
    def dimensions_matrix(self):
        """Displayed dimensions matrix.

        This is a matrix of pairs of items which can be:

        * tuple `(channel_id, feature_idx)`
        * `'time'`

        """
        if len(self.background.dimensions_matrix):
            return self.background.dimensions_matrix
        else:
            return self.visual.dimensions_matrix

    @dimensions_matrix.setter
    def dimensions_matrix(self, value):
        # WARNING: dimensions_matrix should be changed here, in the Canvas,
        # and not in the visual. This is to make sure that the boxes are
        # updated as well.
        self.visual.dimensions_matrix = value
        self.update_dimensions_matrix(value)

    def update_dimensions_matrix(self, matrix):
        n_rows = len(matrix)
        if self.background.features is not None:
            self.background.dimensions_matrix = matrix
        self.boxes.n_rows = n_rows
        self.lasso.n_rows = n_rows
        self.axes.n_rows = n_rows
        self.axes.xs = [0]
        self.axes.ys = [0]
        self._pz.n_rows = n_rows
        self._set_pan_constraints(matrix)
        self.update()

    @property
    def marker_size(self):
        """Marker size."""
        return self.visual.marker_size

    @marker_size.setter
    def marker_size(self, value):
        self.visual.marker_size = value
        self.update()

    def on_draw(self, event):
        """Draw the features in a grid view."""
        gloo.clear(color=True, depth=True)
        self.axes.draw()
        self.background.draw()
        self.visual.draw()
        self.lasso.draw()
        self.boxes.draw()

    keyboard_shortcuts = {
        'marker_size_increase': 'alt++',
        'marker_size_decrease': 'alt+-',
        'add_lasso_point': 'shift+left click',
        'clear_lasso': 'shift+right click',
    }

    def on_mouse_press(self, e):
        control = e.modifiers == ('Control',)
        shift = e.modifiers == ('Shift',)
        if shift:
            if e.button == 1:
                n_rows = self.lasso.n_rows

                box = self._pz._get_box(e.pos)
                self.lasso.box = box

                position = self._pz._normalize(e.pos)
                x, y = position
                x *= n_rows
                y *= -n_rows
                pos = (x, y)
                # pos = self._pz._map_box((x, y), inverse=True)
                pos = self._pz._map_pan_zoom(pos, inverse=True)
                self.lasso.add(pos.ravel())
            elif e.button == 2:
                self.lasso.clear()
            self.update()
        elif control:
            box = self._pz._get_box(e.pos)
            self.emit('enlarge',
                      box=box,
                      dimensions=self.dimensions_matrix[box],
                      )

    def on_key_press(self, event):
        """Handle key press events."""
        coeff = .25
        if 'Alt' in event.modifiers:
            if event.key == '+':
                self.marker_size += coeff
            if event.key == '-':
                self.marker_size -= coeff


#------------------------------------------------------------------------------
# Plotting functions
#------------------------------------------------------------------------------

@_wrap_vispy
def plot_features(features, **kwargs):
    c = FeatureView()
    c.set_data(features, **kwargs)
    return c