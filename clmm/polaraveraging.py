"""@file polaraveraging.py
Functions to compute polar/azimuthal averages in radial bins
"""

try:
    import pyccl as ccl
except:
    pass
import math
import warnings
import numpy as np
import matplotlib.pyplot as plt
import astropy
from astropy.coordinates import SkyCoord
from astropy.cosmology import FlatLambdaCDM
from astropy.table import Table
from astropy import units as u


##############################################################################################
def _astropy_to_CCL_cosmo_object(astropy_cosmology_object):
#ALLOWS TO USE EITHER ASTROPY OR CCL FOR COSMO OBJECT, MAYBE THIS FUNCTION SOULD NOT BE HERE
#adapted from https://github.com/LSSTDESC/CLMM/blob/issue/111/model-definition/clmm/modeling.py
    """Generates a ccl cosmology object from an GCR or astropy cosmology object.
    """
    apy_cosmo = astropy_cosmology_object
    ccl_cosmo = ccl.Cosmology(Omega_c=(apy_cosmo.Odm0-apy_cosmo.Ob0), Omega_b=apy_cosmo.Ob0,
                              h=apy_cosmo.h, n_s=apy_cosmo.n_s, sigma8=apy_cosmo.sigma8,
                              Omega_k=apy_cosmo.Ok0)

    return ccl_cosmo


##############################################################################################
#### Wrapper functions #######################################################################
##############################################################################################
def compute_shear(cluster, geometry="flat", add_to_cluster=True):
    """Computes tangential shear, cross shear, and angular separation

    Parameters
    ----------
    cluster: GalaxyCluster object
        GalaxyCluster object with galaxies
    geometry: str ('flat', 'curve')
        Geometry to be used in the computation of theta, phi
    add_to_cluster: bool
        Adds the outputs to cluster.galcat

    Returns
    -------
    gt: float vector
        tangential shear
    gx: float vector
        cross shear
    theta: float vector
        radius in radians
    """
    if not ('e1' in cluster.galcat.columns and 'e2' in cluster.galcat.columns):
        raise TypeError('shear information is missing in galaxy, must have (e1, e2) or\
                         (gamma1, gamma2, kappa)')
    theta, gt, gx = _compute_shear(cluster.ra, cluster.dec, cluster.galcat['ra'],
                                    cluster.galcat['dec'], cluster.galcat['e1'],
                                    cluster.galcat['e2'], sky=geometry)
    if add_to_cluster:
        cluster.galcat['theta'] = theta
        cluster.galcat['gt'] = gt
        cluster.galcat['gx'] = gx
    return theta, gt, gx


def make_shear_profile(cluster, radius_unit, bins=None, cosmo=None,
                       add_to_cluster=True):
    """Computes shear profile of the cluster

    Parameters
    ----------
    cluster: GalaxyCluster object
        GalaxyCluster object with galaxies
    radiaus_unit:
        Radial unit of the profile, one of 
        ["rad", deg", "arcmin", "arcsec", kpc", "Mpc"]
    bins: array_like, float
        User defined n_bins + 1 dimensional array of bins, if 'None',
        the default is 10 equally spaced radial bins
    cosmo:
        Cosmology object 
    add_to_cluster: bool
        Adds the outputs to cluster.profile

    Returns
    -------
    profile_table: astropy Table
        Table with r_profile, gt profile (and error) and
        gx profile (and error)

    Note
    ----
    Currently, the radial_units are not saved in the profile_table.
    We have to add it somehow.
    """
    if not ('gt' in cluster.galcat.columns and 'gx' in cluster.galcat.columns
            and 'theta' in cluster.galcat.columns):
        raise TypeError('shear information is missing in galaxy must have tangential and cross\
                         shears (gt,gx). Run compute_shear first!')
    radial_values = _theta_units_conversion(cluster.galcat['theta'], radius_unit, z_cl=cluster.z,
                                            cosmo = cosmo)
    r_avg, gt_avg, gt_std = _compute_radial_averages(radial_values, cluster.galcat['gt'].data, bins=bins)
    r_avg, gx_avg, gx_std = _compute_radial_averages(radial_values, cluster.galcat['gx'].data, bins=bins)
    profile_table = Table([r_avg, gt_avg, gt_std, gx_avg, gx_avg],
                          names=('radius', 'gt', 'gt_err', 'gx', 'gx_err'))
    if add_to_cluster:
        cluster.profile = profile_table
        cluster.profile_radius_unit = radius_unit
    return profile_table


def plot_profiles(cluster, r_units=None):
    """Plot shear profiles for validation

    Parameters
    ----------
    cluster: GalaxyCluster object
        GalaxyCluster object with galaxies
    """
    prof = cluster.profile
    if r_units is not None:
        if cluster.profile['radius'].unit is not None:
            warning.warn(('r_units provided (%s) differ from r_units in galcat table (%s) using\
                            user defined')%(r_units, cluster.profile['radius'].unit))
        else:
            r_units = cluster.profile['radius'].unit
    return _plot_profiles(*[cluster.profile[c] for c in ('radius', 'gt', 'gt_err', 'gx', 'gx_err')],
                            r_unit=cluster.profile_radius_unit)

# Maybe these functions should be here instead of __init__
#GalaxyCluster.compute_shear = compute_shear
#GalaxyCluster.make_shear_profile = make_shear_profile
#GalaxyCluster.plot_profiles = plot_profiles

##############################################################################################
#### Internal functions ######################################################################
##############################################################################################
def _compute_theta_phi(ra_l, dec_l, ra_s, dec_s, sky="flat"):
    """Returns the characteristic angles of the lens system

    Add extended description

    Parameters
    ----------
    ra_l, dec_l : float
        ra and dec of lens in decimal degrees
    ra_s, dec_s : array_like, float
        ra and dec of source in decimal degrees
    sky : str, optional
        'flat' uses the flat sky approximation (default) and 'curved' uses exact angles
        if 'flat' is used and any separation is > 1 deg, a warning is raised.

    Returns
    -------
    theta : array_like, float
        Angular separation on the sky in radians
    phi : array_like, float
        Angle in radians, (can we do better)
    """
    if not -360. <= ra_l <= 360.:
        raise ValueError("ra = %f of lens if out of domain"%ra_l)
    if not -90. <= dec_l <= 90.:
        raise ValueError("dec = %f of lens if out of domain"%dec_l)
    if not np.array([-360. <= x_ <= 360. for x_ in ra_s]).all():
        raise ValueError("Object has an invalid ra in source catalog")
    if not np.array([-90. <= x_ <= 90 for x_ in dec_s]).all():
        raise ValueError("Object has an invalid dec in the source catalog")


    if sky == "flat":
        dx = (ra_s - ra_l)*u.deg.to(u.rad) * math.cos(dec_l*u.deg.to(u.rad))
        dy = (dec_s - dec_l)*u.deg.to(u.rad)
        ## make sure absolute value of all RA differences are < 180 deg:
        ## subtract 360 deg from RA angles > 180 deg
        dx[dx>=np.pi] = dx[dx>=np.pi] - 2.*np.pi
        ## add 360 deg to RA angles < -180 deg
        dx[dx<-np.pi] = dx[dx<-np.pi] + 2.*np.pi 
        theta =  np.sqrt(dx**2 + dy**2)
        phi = np.arctan2(dy, -dx)

    #elif sky == "curved":
        #raise ValueError("Curved sky functionality not yet supported!")
        # coord_l = SkyCoord(ra_l*u.deg,dec_l*u.deg)
        # coord_s = SkyCoord(ra_s*u.deg,dec_s*u.deg)
        # theta = coord_l.separation(coord_s).to(u.rad).value
        # SkyCoord method position_angle gives east of north, so add pi/2
        # phi = coord_l.position_angle(coord_s).to(u.rad).value + np.pi/2.
    else:
        raise ValueError("Sky option %s not supported!"%sky)

    if np.any(theta < 1.e-9):
        raise ValueError("Ra and Dec of source and lens too similar")
    if np.any(theta > np.pi/180.):
        warnings.warn("Using the flat-sky approximation with separations > 1 deg may be inaccurate")

    return theta, phi


def _compute_g_t(g1, g2, phi):
    r"""Computes the tangential shear for each source in the galaxy catalog

    Add extended description

    Parameters
    ----------
    g1, g2 : array_like, float
        Ellipticity or shear for each source in the galaxy catalog
    phi: array_like, float
        As defined in comput_theta_phi (readdress this one)

    Returns
    -------
    g_t : array_like, float
        tangential shear (need not be reduced shear)

    Notes
    -----
    g_t = - (g_1 * \cos(2\phi) + g_2 * \sin(2\phi)) [cf. eqs. 7-8 of Schrabback et al. 2018, arXiv:1611.03866]
    """
    if type(g1) != type(g2):
        raise ValueError("g1 and g2 should both be array-like of same length or float-like")
    if type(g1) != type(phi):
        raise ValueError("shear and position angle should both be array-like of same length or float-like")
    if np.sum(phi<-np.pi) > 0:
        raise ValueError("Position angle should be in radians")
    if np.sum(phi>=2*np.pi) > 0:
        raise ValueError("Position angle should be in radians")
    if (np.shape(g1) != np.shape(g2)):
        raise ValueError("The lengths of shear1 and shear2 do not match.")
    if (np.shape(g1) != np.shape(phi)):
        raise ValueError("The lengths of shear1 and phi do not match.")
    if (np.shape(g2) != np.shape(phi)):
        raise ValueError("The lengths of shear2 and phi do not match.")
    g_t = - (g1*np.cos(2*phi) + g2*np.sin(2*phi))
    return g_t


def _compute_g_x(g1, g2, phi):
    r"""Computes cross shear for each source in galaxy catalog

    Parameters
    ----------
    g1, g2,: array_like, float
        ra and dec of the lens (l) and source (s)  in decimal degrees
    phi: array_like, float
        As defined in comput_theta_phi

    Returns
    -------
    gx: array_like, float
        cross shear

    Notes
    -----
    Computes the cross shear for each source in the catalog as:
    g_x = - g_1 * \sin(2\phi) + g_2 * \cos(2\phi)    [cf. eqs. 7-8 of Schrabback et al. 2018, arXiv:1611.03866]
    """
    if type(g1) != type(g2):
        raise ValueError("g1 and g2 should both be array-like of same length or float-like")
    if type(g1) != type(phi):
        raise ValueError("shear and position angle should both be array-like of same length or float-like")
    if np.sum(phi<-np.pi) > 0:
        raise ValueError("Position angle should be in radians")
    if np.sum(phi>=2*np.pi) > 0:
        raise ValueError("Position angle should be in radians")
    if (np.shape(g1) != np.shape(g2)):
        raise ValueError("The lengths of shear1 and shear2 do not match.")
    if (np.shape(g1) != np.shape(phi)):
        raise ValueError("The lengths of shear1 and phi do not match.")
    if (np.shape(g2) != np.shape(phi)):
        raise ValueError("The lengths of shear2 and phi do not match.")
    g_x = - g1 * np.sin(2*phi) + g2 *np.cos(2*phi)
    return g_x


def _compute_shear(ra_l, dec_l, ra_s, dec_s, g1, g2, sky="flat"):
    r"""Wrapper that returns tangential and cross shear along with radius in radians

    Parameters
    ----------
    ra_l, dec_l: float
        ra and dec of lens in decimal degrees
    ra_s, dec_s: array_like, float
        ra and dec of source in decimal degrees
    g1, g2: array_like, float
        shears or ellipticities from galaxy table
    sky: str, optional
        'flat' uses the flat sky approximation (default) and 'curved' uses exact angles

    Returns
    -------
    gt: array_like, float
        tangential shear
    gx: array_like, float
        cross shear
    theta: array_like, float
        Angular separation between lens and sources

    Notes
    -----
    Computes the cross shear for each source in the galaxy catalog as:
    g_x = - g_1 * \sin(2\phi) + g_2 * \cos(2\phi)
    g_t = - (g_1 * \cos(2\phi) + g_2 * \sin(2\phi)) [cf. eqs. 7-8 of Schrabback et al. 2018, arXiv:1611.03866]
    """
    theta, phi = _compute_theta_phi(ra_l, dec_l, ra_s, dec_s, sky=sky)
    g_t = _compute_g_t(g1, g2, phi)
    g_x = _compute_g_x(g1, g2, phi)
    return theta, g_t, g_x


def _theta_units_conversion(theta, units, z_cl=None, cosmo=None):
    """Converts theta from radian to whatever units specified in units

    Parameters
    ----------
    theta : float
        We assume the input unit is radian. Theta is angular seperation between source galaxies and the cluster center in 2D image.
    units : string
        Output unit you would like to convert to. 
    repeats for all parameters

    Returns
    -------
    radius : float
        Theta in the converted unit you want to.

    Notes
    -----
    This stuff below is left over, replace it above and remove this.
    units: one of ["rad", deg", "arcmin", "arcsec", kpc", "Mpc"]
    cosmo : cosmo object
    z_cl : cluster redshift
    """
    theta = theta * u.rad

    units_bank = {
        "rad": u.rad,
        "deg": u.deg,
        "arcmin": u.arcmin,
        "arcsec": u.arcsec,
        "kpc": u.kpc,
        "Mpc": u.Mpc,
        }

    if units in units_bank:
        units_ = units_bank[units]
        if units[1:] == "pc":
            if isinstance(cosmo,astropy.cosmology.core.FlatLambdaCDM): # astropy cosmology type
                Da = cosmo.angular_diameter_distance(z_cl).to(units_).value
            elif isinstance(cosmo, ccl.core.Cosmology): # astropy cosmology type
                Da = ccl.comoving_angular_distance(cosmo, 1/(1+z_cl)) / (1+z_cl) * u.Mpc.to(units_)
            else:
                raise ValueError("cosmo object (%s) not an astropy or ccl cosmology"%str(cosmo))
            return theta.value*Da
        else:
            return theta.to(units_).value
    else:
        raise ValueError("units (%s) not in %s"%(units, str(units_bank.keys())))
    if z_cl is None:
        raise ValueError("To compute physical units, z_cl must not be None")

def make_bins(rmin, rmax, n_bins=10, log_bins=False):
    """Define equal sized bins with an array of n_bins+1 bin edges

    Parameters
    ----------
    rmin, rmax,: float
        minimum and and maximum range of data (any units)
    n_bins: float
        number of bins you want to create
    log_bins: bool
        set to 'True' equal sized bins in log space

    Returns
    -------
    binedges: array_like, float
        n_bins+1 dimensional array that defines bin edges
    """
    if rmax<rmin:
        raise ValueError("rmax should be larger than rmin")
    if n_bins <= 0:
        raise ValueError("n_bins must be > 0")
    if type(log_bins)!=bool:
        raise TypeError("log_bins must be type bool")
    if type(n_bins)!=int:
        raise TypeError("You need an integer number of bins")

    if log_bins==True:
        rmin = np.log(rmin)
        rmax = inp.log(rmax)
        logbinedges = np.linspace(rmin, rmax, n_bins+1, endpoint=True)
        binedges = np.exp(logbinedges)
    else:
        binedges = np.linspace(rmin, rmax, n_bins+1, endpoint=True)

    return binedges


def _compute_radial_averages(radius, g, bins=None):
    """Returns astropy table containing shear profile of either tangential or cross shear

    Parameters
    ----------
    radius: array_like, float
        Distance (physical or angular) between source galaxy to cluster center
    g: array_like, float
        Either tangential or cross shear (g_t or g_x)
    bins: array_like, float
        User defined n_bins + 1 dimensional array of bins, if 'None', the default is 10 equally spaced radial bins

    Returns
    -------
    r_profile: array_like, float
        Centers of radial bins
    g_profile: array_like, float
        Average shears per bin
    gerr_profile: array_like, float
        Standard deviation of shear per bin
    """
    if not isinstance(radius, (np.ndarray)):
        raise TypeError("radius must be an array")
    if not isinstance(g, (np.ndarray)):
        raise TypeError("g must be an array")
    if len(radius) != len(g):
        raise TypeError("radius and g must be arrays of the same length")
    if np.any(bins) == None:
        nbins = 10
        bins = np.linspace(np.min(radius), np.max(radius), nbins)

    g_profile = np.zeros(len(bins) - 1)
    gerr_profile = np.zeros(len(bins) - 1)
    r_profile =  np.zeros(len(bins) - 1)

    if np.amax(radius) > np.amax(bins):
        warnings.warn("maximum radius must be within range of bins")
    if np.amin(radius) < np.amin(bins):
        warnings.warn("minimum radius must be within the range of bins")

    for i in range(len(bins)-1):
        cond = (radius>= bins[i]) & (radius < bins[i+1])
        index = np.where(cond)[0]
        r_profile[i] = np.average(radius[index])
        g_profile[i] = np.average(g[index])
        if len(index) != 0:
            gerr_profile[i] = np.std(g[index]) / np.sqrt(float(len(index)))
        else:
            gerr_profile[i] = np.nan

    return r_profile, g_profile, gerr_profile


def _plot_profiles(r, gt, gterr, gx=None, gxerr=None, r_unit=""):
    """Plot shear profiles for validation

    Parameters
    ----------
    r: array_like, float
        radius
    gt: array_like, float
        tangential shear
    gterr: array_like, float
        error on tangential shear
    gx: array_like, float
        cross shear
    gxerr: array_like, float
        error on cross shear
    r_unit: string
	unit of radius
	
    """
    fig, ax = plt.subplots()
    ax.plot(r, gt, 'bo-', label="tangential shear")
    ax.errorbar(r, gt, gterr, label=None)

    try:
        plt.plot(r, gx, 'ro-', label="cross shear")
        plt.errorbar(r, gx, gxerr, label=None)
    except:
        pass

    ax.legend()
    if r_unit is not None:
    	ax.set_xlabel("r [%s]"%r_unit)
    ax.set_ylabel('$\\gamma$')

    return(fig, ax)
