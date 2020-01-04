import numpy as np
from sklearn.base import TransformerMixin, BaseEstimator
from sklearn.decomposition import PCA
from sklearn.linear_model import LinearRegression
from statsmodels.tsa.stattools import coint

from sklearn.utils.validation import (
    check_array,
    check_is_fitted,
    assert_all_finite
)

from .stat import StationarityTest
from ._aeg_pca import aeg_pca
from ._utils import rms


class CointAnalysis(BaseEstimator, TransformerMixin):
    """
    Cointegration Analysis.

    Parameters
    ----------
    - method : {'AEG'}, default='AEG'
        Test method.
        If 'AEG', Augmented Dickey-Fuller test.
    - axis : {'0', '1', 'PCA'}, default='0'
        How to obtain the spread.
        If '0', regress 0th series to 1st series and use the residual.
        If '1', regress 1st series to 0th series and use the residual.
        If 'PCA', carry out principal component analysis and use PC1.
    - trend : {'c', 'nc'}, default='c'
        Specifies the presence of constant term in the residual.
        If 'c', Allow residual to have a constant term.
        If 'nc', Prohibit residual to have a constant term.
    - adjust_mean : bool, default True
        If True, subtract mean when evaluating the spread.
    - adjust_std : bool, default True
        If True, normalize std to unity when evaluating the spread.

    Attributes
    ----------
    - coef_ : array, shape (2, )
        Coefficients of time-series in the cointegration equation.
        Also known as a cointegration vector.
    - mean_ : float
        Constant term in the cointegration equation.
        If `trend = 'nc'`, always 0.0.
    - std_ : float
        Standard deviation of the spread.

    Notes
    -----
    Given a pair of time-series is cointegrated, `coef_ = (b0, b1)` and
    `mean_ = c` implies that
    ```spread = b0 * X[:, 0] + b1 * X[:, 1] - c```
    is stationary around 0. Std of this spread is given by 'std_'.

    Examples
    --------
    >>> import numpy as np
    >>> np.random.seed(42)
    >>> x0 = np.random.rand(1000).cumsum()
    >>> x1 = 2 * x0 + np.random.rand(1000)
    >>> X = np.array([x0, x1]).T
    >>> coint = CointAnalysis()
    >>> coint.pvalue(X)
    1.92836794233469e-18

    >>> coint.fit(X)
    >>> coint.coef_
    array([-1.99831281,  1. ])
    >>> coint.mean_
    0.011497817617380335
    >>> coint.std_
    0.9488566139638319

    >>> coint.transform(X)
    array([-1.50289357, ...])
    # = (X.dot(coint.coef_) - coint.mean_) / coint.std_)
    """
    def __check_params(self):
        if self.method not in ('AEG', ):
            raise ValueError(f'Invalid method: {self.method}.')
        if self.axis not in ('0', '1', 'PCA'):
            raise ValueError(f'Invalid axis: {self.axis}.')
        if self.trend not in ('c', 'nc'):
            raise ValueError(f'Invalid trend: {self.trend}.')

    def __init__(self,
                 method='AEG',
                 axis='0',
                 trend='c',
                 adjust_mean=True,
                 adjust_std=True):
        """Initialize self."""
        self.method = method
        self.axis = axis
        self.trend = trend
        self.adjust_mean = adjust_mean
        self.adjust_std = adjust_std

    def fit(self, X, y=None):
        """
        Fit the model with X.

        Parameters
        ----------
        - X : array-like, shape (n_samples, 2)
            A pair of time-series, where n_samples is the number of samples.
        - y : None
            Ignored.

        Returns
        -------
        self
        """
        self.__check_params()
        X = check_array(X)
        assert_all_finite(X)
        if X.shape[1] != 2:
            raise ValueError('X.shape[1] should be 2.')

        if self.axis in ('0', '1'):
            if self.axis == '0':
                X, y = X[:, 0].reshape(-1, 1), X[:, 1]
            if self.axis == '1':
                X, y = X[:, 1].reshape(-1, 1), X[:, 0]

            if self.trend == 'c':
                fi = True  # fit intercept
            if self.trend == 'nc':
                fi = False

            reg = LinearRegression(fit_intercept=fi).fit(X, y)

            if self.axis == '0':
                self.coef_ = np.array([- reg.coef_[0], 1.0])
            if self.axis == '1':
                self.coef_ = np.array([1.0, - reg.coef_[0]])

            self.mean_ = getattr(reg, 'intercept_', 0.0)
            self.std_ = (y - reg.predict(X)).std()

        if self.axis == 'PCA':
            if self.trend == 'c':
                pca = PCA(n_components=2).fit(X)
                self.coef_ = pca.components_[1]
                self.mean_ = pca.mean_.dot(self.coef_)
                self.std_ = np.sqrt(pca.explained_variance_[1])
            if self.trend == 'nc':
                # This is pseudo-PCA, without adjusting the origin to the
                # center of samples.
                rms0 = rms(X[:, 0])
                rms1 = rms(X[:, 1])
                self.coef_ = np.array([1 / rms0, -1 / rms1])
                self.mean_ = 0.0
                self.std_ = X.dot(self.coef_).std()

        return self

    def transform(self, X):
        """
        Return the cointegration spread.

        Parameters
        ----------
        - X : array-like, shape (n_samples, 2)
            A pair of time-series, where n_samples is the number of samples.
        - y : None
            Ignored.

        Returns
        -------
        X_spread : array-like, shape (n_samples, )
            Spread.
            If `self.adjust_mean` and/or `self.adjust_std` are True,
            mean and/or std are adjusted to 1.0 and 0.0.
        """
        check_is_fitted(self, ['coef_', 'mean_', 'std_'])
        X = check_array(X)
        assert_all_finite(X)
        if X.shape[1] != 2:
            raise ValueError('X.shape[1] should be 2.')

        spread = X.dot(self.coef_)

        if self.adjust_mean:
            spread -= self.mean_
        if self.adjust_std:
            if not self.std_ < 10e-10:
                spread /= self.std_
            else:
                raise RuntimeWarning('Did not normalize the spread '
                                     'because std < 10e-10.')

        return spread

    def pvalue(self, X, stat_method='ADF', stat_pvalue=.05):
        """
        Return p-value of cointegration test.
        Null-hypothesis is no cointegration.

        Notes
        -----
        If X[:, 0] or X[:, 1] is stationary as such, return `np.nan`.

        Parameters
        ----------
        - X : array-like, shape (n_samples, 2)
            A pair of time-series, where n_samples is the number of samples.
        - stat_method : {'ADF'}, default 'ADF'
            Method of stationarity test.
            If 'ADF', Augmented Dickey-Fuller test.
        - stat_pvalue : float, default .05
            Threshold of p-value of stationarity test.

        Returns
        -------
        pvalue
        """
        self.__check_params()
        X = check_array(X)
        assert_all_finite(X)
        if X.shape[1] != 2:
            raise ValueError('X.shape[1] should be 2.')

        # Stationarity test
        stat = StationarityTest(method=stat_method, regression='c')
        if stat.is_stationary(X[:, 0], stat_pvalue) \
                or stat.is_stationary(X[:, 1], stat_pvalue):
            return np.nan

        # Cointegration test
        if self.axis in ('0', '1'):
            if self.axis == '0':
                X0, X1 = X[:, 0], X[:, 1]
            if self.axis == '1':
                X0, X1 = X[:, 1], X[:, 0]

            if self.method == 'AEG':
                _, pvalue, _ = coint(X0, X1, trend=self.trend)
            if self.method == 'KPSS':
                _, pvalue, _ = np.nan, np.nan, np.nan  # TODO not implemented
            if self.method == 'Johansen':
                _, pvalue, _ = np.nan, np.nan, np.nan  # TODO not implemented

        if self.axis == 'PCA':
            if self.method == 'AEG':
                _, pvalue, _ = aeg_pca(X[:, 0], X[:, 1], trend=self.trend)
            if self.method == 'KPSS':
                _, pvalue, _ = np.nan, np.nan, np.nan  # TODO not implemented
            if self.method == 'Johansen':
                _, pvalue, _ = np.nan, np.nan, np.nan  # TODO not implemented

        return pvalue
