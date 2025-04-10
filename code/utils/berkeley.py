import glob
import warnings

import numpy as np
import pandas as pd
import xarray as xr

from . import utils

# see https://github.com/pydata/xarray/issues/7730
xr.set_options(use_flox=False)

DATA_ROOT = "../data/BerkeleyEarth"
VERSION = "v2025"

def _get_filenames(variable, version):
    
    decade = "*"

    path = f"{DATA_ROOT}/{version}/raw/{variable}/Complete_{variable}_Daily_LatLong1_{decade}.nc"
    return sorted(glob.glob(path))

def _fix_dataset(ds):

    # convert integer dates to datetime
    df = ds[["year", "month", "day"]].to_pandas()
    time = pd.to_datetime(df).values

    # remove unnecessary time variables
    ds = ds.drop_vars(["year", "month", "day", "date_number"])

    ds = ds.assign_coords(time=time)

    ds = ds.rename(longitude="lon", latitude="lat")

    return ds

def read_last_decade(variable, version=VERSION):

    files = _get_filenames(variable, version)

    ds_orig = xr.open_dataset(files[-1])

    return _fix_dataset(ds_orig)



def read_full(variable, version=VERSION):
    """read full Berkeley Earth data, clean time and coord names"""

    files = _get_filenames(variable, version)

    ds_orig = utils.open_mfdataset(files, combine="nested", concat_dim="time")

    return _fix_dataset(ds_orig)


def read(variable, time_period, remove_antarctica=True, version=VERSION):
    """read Berkeley Earth data, removing unneded parts"""

    ds = read_full(variable, version=version)

    if remove_antarctica:
        ds = ds.sel(lat=slice(-60, None))

    # remove data before 1950
    # data availibility is generally low before
    ds = ds.sel(time=time_period)

    # make sure there is no incomplete year of data
    if ds.time.dt.dayofyear[-1] < 365:
        last_day = ds.time[-1].dt.strftime(r"%Y-%m-%d").item()
        warnings.warn(f"Last day not at end of year: {last_day}")

    return ds


def get_filename_post(variable, post, version=VERSION):
    """filename for postprocessed Berkeley Earth data"""

    return f"{DATA_ROOT}/{version}/post/{variable}/{variable}_{post}.nc"


def read_post(variable, post, version=VERSION):
    """read postprocessed Berkeley Earth data"""

    filename = get_filename_post(variable, post, version=version)

    return xr.open_dataset(filename)


def read_globmean(ref_period, version=VERSION):
    """read Berkeley Earth global mean temperature"""

    df = pd.read_csv(
        f"{DATA_ROOT}/{version}/raw/Land_and_Ocean_summary.txt",
        header=None,
        sep=r'\s+',
        na_values="NaN",
        skipinitialspace=True,
        comment="%",
        index_col=0,
        usecols=[0, 1],
    )

    df.columns = ["ann"]

    df.index.name = "year"

    globmean = df.to_xarray().ann

    if ref_period is not None:
        globmean = utils.calc_anomaly(globmean, ref_period)

    return globmean


def extend_climatology(ds):
    """add additional day to climatology for leap years"""

    climatology = ds.climatology

    # rename for consistency with xarray
    climatology = climatology.rename(day_number="dayofyear")

    # we need to take care of leap years
    # append one day as the average of 01.01 and 31.12
    c366 = climatology.isel(dayofyear=[0, -1]).mean("dayofyear")

    climatology = xr.concat([climatology, c366], dim="dayofyear")

    # add explicit coordinates
    dayofyear = np.arange(1, 367)
    climatology = climatology.assign_coords(dayofyear=dayofyear)

    return climatology


def add_climatology(ds):
    """add climatology back to daily data, required for TXx & TNn"""

    climatology = extend_climatology(ds)

    # add seasonal cycle to anomalies
    ds_seas = ds.temperature.groupby("time.dayofyear") + climatology

    ds_seas = ds_seas.drop_vars("dayofyear")

    return ds_seas


def land_without_any_data(ds, data_variable="temperature"):
    """find grid points that never have any data"""

    da = ds[data_variable] if isinstance(ds, xr.Dataset) else ds

    has_no_data_on_land = da.isnull().all("time") & (ds.land_mask > 0)

    return has_no_data_on_land


def clean_landmask(ds, data_variable="temperature"):
    """remove gridpoints from landmask that never have any data"""

    has_no_data_on_land = land_without_any_data(ds, data_variable=data_variable)

    land_mask = ds.land_mask.where(~has_no_data_on_land, 0)

    return land_mask


def annual_data_availability(da):
    """calculate fraction of days that have data each year"""

    # get number of days per year
    n_days_per_year = da.time.dt.dayofyear.groupby("time.year").max()

    # fraction of data per year
    data_fraction = da.notnull().groupby("time.year").sum() / n_days_per_year

    return data_fraction


def require_valid(data, data_notnull, valid_days, valid_years):
    """require data has at least 'valid_days' and 'valid_years' data

    Parameters
    ----------
    data : xr.DataArray
        DataArray to remove gridpoints with invalid data.
    data_notnull : xr.DataArray
        DataArray indication the fraction of valid data at each grid point each
        year. Must be derived using ``annual_data_availability``.
    valid_days : float
        Fraction of valid days required each year (must be in 0..1).
    valid_years : float
        Fraction of valid years required for each grid point (must be in 0..1).
    """

    data = data.where(data_notnull > valid_days)

    n_years = data.year.size

    sel = data.notnull().sum("year") > (n_years * valid_years)

    data = data.where(sel)

    return data
