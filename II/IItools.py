import astropy
import numpy as np
from matplotlib.colors import LogNorm
import matplotlib.pyplot as plt
from astropy.visualization import astropy_mpl_style
import astropy.units as u
from astropy.time import Time
from II import IImodels
from astroquery.vizier import Vizier
import numba
from scipy.optimize import curve_fit

# @numba.jit(parallel=True)
def datcompress(A,chunk, chunkavail):
    return np.array([(A[ii * chunk:(ii + 1) * chunk]).mean(axis=0) for ii in range(chunkavail)])

# @numba.jit(parallel=True)
# def datcompress(A,timmeans,chunk, chunkavail):
#     for ii in range(chunkavail):
#         timmeans[ii] = A[ii * chunk:(ii + 1) * chunk].mean()
#     return timmeans
def radial_profile(data, center=None):
    """
    Calculate a circular 1D radial profile given a 2D array
    :param data: The 2D image you wish to take a radial profile of
    :param center: the location of the center of the image. If None, it will assume the center of the image is in the
    center of the array
    :return: The 1D radial profile of your data
    """
    if center == None:
        center = (data.shape[0] / 2, data.shape[1] / 2)

    y, x = np.indices((data.shape))
    r = np.sqrt((x - center[0]) ** 2 + (y - center[1]) ** 2)
    r = r.astype(np.int)

    tbin = np.bincount(r.ravel(), data.ravel())
    nr = np.bincount(r.ravel())
    radialprofile = tbin / nr
    return radialprofile

def proj_baseline(lat, dec, hour):
    """
    The function used to calculate coverage of a projected baseline on the uv-plane
    :param lat: The latitude of the observatory in radians
    :param dec: The declenation of your target in radians
    :param hour: The hourangle of your target in radians
    :return: The calculated projected baseline
    """
    ar = np.array
    Bp1 = ar([ar([-np.sin(lat) * np.sin(hour), np.cos(hour), np.cos(lat) * np.sin(hour)]),

              ar([np.sin(lat) * np.cos(hour) * np.sin(dec) + np.cos(lat) * np.cos(dec), np.sin(hour) * np.sin(dec),
                  -np.cos(lat) * np.cos(hour) * np.sin(dec) + np.sin(lat) * np.cos(dec)]),

              ar([-np.sin(lat) * np.cos(hour) * np.cos(dec) + np.cos(lat) * np.sin(dec),
                  -np.sin(hour) * np.cos(dec),
                  np.cos(lat) * np.cos(hour) * np.cos(dec) + np.sin(lat) * np.sin(dec)])])

    return Bp1


def uv_tracks(lat, dec, hours, Bn, Be, Bu):
    """
    Calculate the uv-plane coverage for all possible baselines for a given observatory
    :param lat: The latitude of your observatory
    :param dec: The declenation of your target in radians
    :param hours: The hourangle of your target in radians
    :param Bn: The North South baselines of the observatory in meters
    :param Be: The East West baselines of the observatory in meters
    :param Bu: The Elevation baselines of the observatory in meters
    :return: The calculated tracks along with the reflected tracks as it's Visibility squared
    """

    baselines = np.transpose(np.array([Bn, Be, Bu]))
    track = np.array([np.dot(proj_baseline(lat,dec,hour), baselines) for hour in hours])

    ref_track = np.array([np.dot(proj_baseline(lat, dec, hour), -baselines) for hour in hours])
    return track, ref_track

def array_baselines(tel_locs):
    """
    Use the telescope locations relative to each other to calculate all possible baselines with the given array.
    :param tel_locs: The x,y,z coordinates of your telescopes relative to a central position, in meters.
    :return: the calculated baselines for the given telescope positions
    """
    n = len(tel_locs)
    N = n*(n-1)/2
    baselines = []
    tel_names = []
    for i in range(n):
        for j in range(1,n-i):
            baselines.append(tel_locs[i] - tel_locs[i+j])
            tel_names.append("T%sT%s"%(i+0,i+j + 0))
    return baselines, tel_names



def track_coverage(tel_tracks, airy_func):
    """
    Calculate the amount of coverage for given orders of an airy disk, as well as the amplitude range.
    :param tel_tracks: The uv-track coverage calculated for a target at an observatory
    :param airy_func: the Airy Function of your target
    :return:
    """
    x_0 = airy_func.x_0.value
    y_0 = airy_func.y_0.value
    ranges = []
    amps = []
    r_0 = airy_func.radius.value
    for i, track in enumerate(tel_tracks):
        utrack = track[0][:, 0] + x_0
        vtrack = track[0][:, 1] + y_0
        airy_amp = airy_func(utrack, vtrack)
        airy_radius = np.sqrt((utrack - x_0) ** 2 + (vtrack - y_0) ** 2)
        amps.append(airy_amp)
        ranges.append([np.min(airy_radius), np.max(airy_radius)])

    merged_ranges = interval_merger(ranges)
    r0_cov = 0
    r1_cov = 0
    r2_cov = 0
    r0_amp = 0
    r1_amp = 0
    for ran in merged_ranges:
        r0_cov = r0_cov + np.ptp(getIntersection(ran,[0,r_0]))
        r1_cov = r1_cov + np.ptp(getIntersection(ran,[r_0,2*r_0]))
        r2_cov = r2_cov + np.ptp(getIntersection(ran,[2*r_0,3*r_0]))
    if r0_cov > 0:
        r0_amp = curve_amplitude(merged_ranges,0,r_0,airy_func,x_0,y_0)
    if r1_cov > 0:
        r1_amp = curve_amplitude(merged_ranges,r_0,r_0*2,airy_func,x_0,y_0)
    r_amp = np.ptp(amps)

    return

def curve_amplitude(ranges, st, end, airy_func, x_0, y_0):
    """
    Find the amplitude range of your uv-plane coverage for your targets Airy Disk
    :param ranges: The ranges of the baseline coverage you have in meters
    :param st: Used to define the location of the start of the order you want to analyze
    :param end: Used to define the location of the end of the order you want to analyze
    :param airy_func: The airy function of the target you wish to analyze
    :param x_0: The x coordinate of the center of your airy disk
    :param y_0: The y coordinate of the center of your airy disk
    :return: The ampliude range of the baseline coverage for the order that was specified by st and end
    """
    r0_range = [getIntersection(rang, [st, end]) for rang in ranges if getIntersection(rang, [st, end]) is not 0]
    minr = np.min(r0_range)
    maxr = np.max(r0_range)
    high = airy_func(y_0, minr + x_0)
    low = airy_func(y_0, maxr + x_0)
    return high - low

def track_error(sig0, m0, m, T_0, T):
    """
    A function that allows dynamic error calculation, given an initial error measurment
    :param sig0: The empiracally calculated error for a given array of telescopes
    :param m0: The magnitude used in calculating sig0
    :param m: The magniutde of the target being analyzing
    :param T_0: The integration time used in calculating sig0
    :param T: The integration time used when observing a target
    :return:
    """
    return sig0 * (2.512) ** (m - m0) * (T_0 / T) ** .5

def trap_w_err(numerical_f, r, erra, errb):
    """
    Integrate using the trapeoidal rule and include the uncertainty in such a calculation
    :param numerical_f: A numerical function that is going to be integrated
    :param r: The x values of your function
    :param erra: The error associated with your numerical function
    :param errb: The error associated with your numercial function
    :return:
    """
    fa = numerical_f[:-1]
    fb = numerical_f[1:]
    ra = r[:-1]
    rb = r[1:]
    C = (fa + fb)/2
    dr = rb-ra
    I = dr * C
    Ierr = dr*np.sqrt(erra**2 + errb*2)*C
    return I, Ierr, dr


def trapezoidal_average(num_f):
    """
    Using integration, take the average of a numerical function using the trapezoidal rule
    :param num_f:
    :return:
    """
    if len(num_f)>1:
        fa = num_f[:-1]
        fb = num_f[1:]
        func_avg = (fa + fb)/2
    else:
        func_avg = np.mean(num_f)
    return func_avg


def interval_merger(intervals):
    """
    Using a given set of intervals, merge overlapping intervals
    :param intervals:
    :return:
    """
    sint = sorted(intervals, key=lambda i: i[0])
    out = [sint[0]]
    for current in sorted(intervals, key=lambda i: i[0]):
        previous = out[-1]
        if current[0] <= previous[1]:
            previous[1] = max(previous[1], current[1])
        else:
            out.append(current)
    return out

def getIntersection(interval_1, interval_2):
    """
    Determine where two given intervals overlap
    :param interval_1: The starting interval
    :param interval_2: The ending interval
    :return:
    """
    st = np.max([interval_1[0], interval_2[0]])
    end = np.min([interval_1[1], interval_2[1]])
    if st < end:
        return [st, end]
    return 0

def IIbootstrap_analysis_airyDisk(tel_tracks, airy_func, star_err, guess_diam, wavelength, runs):
    """
    This is a custom Monte Carlo analysis which creates model data using the given input parameters, adds gaussian error
    to the simulated data, adds gaussian error to guess_r, and then attempts to fit the simulated data with error added
    using the guess_r with the error added to see if the fit will converge to the original guess_r. If the fit cannot
    coverge to the original guess_r over many different fits, the given target is likely not useful to observe.

    If you wish to add different analytical models, you can use this function as a template. The only part which would
    have to be changed is the fitting of an airy disk.
    :param tel_tracks: The uv-coverage tracks for a given target
    :param airy_func: The Airy Function of for a given target
    :param star_err: The error associated with measuring a given target with a given observatory
    :param guess_diam: The initial guess diameter of your target in meters
    :param wavelength: The wavlength of the used filter in meters
    :param runs: The amount of simulations to perform
    :return: The fitted angular diameters of your target in radians
    """
    fit_diams = []
    fiterrs = []
    failed_fits = 0
    for i in range(0, runs):
        #this function creates data structers which are an averaged integration of your visibility function transformed
        #radially into a 1D function. As long as your visibility_function has the appropiate data members, it should
        #function with any visibility model.
        rads, amps, avgrad, avgamp = IImodels.visibility2dTo1d(tel_tracks=tel_tracks, visibility_func=airy_func,
                                                               x_0=airy_func.x_0.value, y_0=airy_func.y_0.value)


        try:
            #If you wish to add different analytical models, simply write a function that replaces fit_airy_avg below
            #Be sure to understand the data structures produced by airy2dTo1d and to include the new model in a different
            #function to allow for readability
            airy_fitr, airy_fiterr, sigmas = IImodels.fit_airy_avg(rads=rads, avg_rads=avgrad,
                                                                   avg_amps=avgamp + np.random.normal(0, star_err,avgamp.shape),
                                                                   err=star_err,
                                                                   guess_r=guess_diam + np.random.normal(0,guess_diam / 5))

            #if the fit error is extremly high, count it as a failed fit
            if airy_fitr[0] > guess_diam*10 or airy_fitr[0] < guess_diam*.1:
                fit_diams.append(np.nan)
                fiterrs.append(np.nan)
                failed_fits += 1
                continue

            #calulate the error necessary and append to the necessary arrays
            fit_err = np.sqrt(np.diag(airy_fiterr))[0] / airy_fitr[0]
            fit_diams.append(airy_fitr[0])
            fiterrs.append(fit_err)

        except Exception as e:
            #use a try, except block for when the fit fails and add a failed fit when this happens
            print(e)
            print("The fit failed")
            failed_fits +=1
            fit_diams.append(np.nan)
            fiterrs.append(np.nan)

    #create numpy arrays for easy appending to the Astropy table in a later date
    npdiams = np.array(fit_diams)
    nperrs = np.array(fiterrs)

    #if a negative value for the diameter was determined, it's obviously wrong and replace the value with nan
    neg = np.where(npdiams < 0)
    npdiams[neg] = np.nan
    nperrs[neg] = np.nan

    return npdiams, nperrs, failed_fits

def chisq(obs, exp, error):
    return np.sum((obs - exp) ** 2 / (error ** 2))

def chi_square_anal(airy_func, tel_tracks, guess_r, star_err, ang_diam):
    from scipy.stats import chisquare
    rads, amps, avgrad, avgamp = IImodels.visibility2dTo1d(tel_tracks=tel_tracks, visibility_func=airy_func,
                                                           x_0=airy_func.x_0.value, y_0=airy_func.y_0.value)
    yerr = np.random.normal(0, star_err, avgamp.shape)
    rerr = np.random.normal(0, guess_r / 5)
    airy_fitr, airy_fiterr, sig = IImodels.fit_airy_avg(rads=rads, avg_rads=avgrad, avg_amps=avgamp + yerr,
                                                        err=star_err, guess_r=guess_r + rerr)

    mincon = .5
    maxcon = 1.5
    min_bound = .1
    max_bound = 3

    fit_vals = np.linspace(min_bound*guess_r, max_bound*guess_r, num=300)

    xvals = np.linspace(0, guess_r * 2)
    chis = []

    def airy_avg(xr, r):
        mod_Int = np.array([trapezoidal_average(IImodels.airy1D(rad, r)) for rad in rads])
        return mod_Int.ravel()

    # plt.plot(avgrad.ravel(), airy_avg(rads, guess_r), 'o')

    perfect_dat = airy_avg(rads, guess_r)

    perfect_dat = airy_avg(rads, guess_r)
    for val in fit_vals:
        new_chi = chisq(airy_avg(rads, val), perfect_dat, star_err)
        chis.append(new_chi)

    plot_vals = np.linspace(ang_diam * mincon, ang_diam * maxcon, num=300)

    # plt.plot(plot_vals, chis)
    return plot_vals[::-1],chis

def amp_anal(data, odp_corr, baseline, start, end, order=3, g2width=0.9):

    cut_opd_correction = odp_corr - start
    cutdata = data[:, start:end]
    g2shape = cutdata.shape

    def g2_sig_amp_ravel(x, *args):
        polyterms = [term for term in args]
        g2amp_poly = np.poly1d(polyterms)
        g2amp_model = g2amp_poly(x)
        ravelg2 = g2_sig_surface(g2width, g2amp_model, cut_opd_correction, g2shape).ravel()
        return ravelg2

    time = np.arange(cutdata.shape[0])

    guess_par = np.zeros(order)
    g2fitpar, g2fiterr = curve_fit(f=g2_sig_amp_ravel,
                                   xdata=baseline,
                                   ydata=cutdata.ravel(),
                                   p0=guess_par)
    amps = np.poly1d(g2fitpar)(baseline)
    return amps, cut_opd_correction, cutdata, g2fitpar
def opd_correction_new(data, opd, baseline, tcors, datstart=40, datend=90, order=4, highorder=4, g2width=0.9):
    steps = len(tcors)
    amp_means = np.zeros(steps)
    opdstart_tcor = np.zeros(len(tcors))
    for i in range(steps):
        opdcorrected = opd + tcors[i]
        amps, cut_opd_correction, cutdata , polypars = amp_anal(data, opdcorrected, baseline, datstart, datend, order=order, g2width=g2width)
        amp_means[i] = amps.mean()
        opdstart_tcor[i] = opdcorrected[0]
    peak_arg = np.argmax(amp_means)

    tcorr = opdstart_tcor[peak_arg]
    bestfitamp, _, _, polypars = amp_anal(data, opd + tcorr, baseline, datstart, datend, order=order)
    bestfitamp_highpoly, _, _, polypars_high = amp_anal(data, opd + tcorr, baseline, datstart, datend,
                                                        order=highorder)
    tshift = tcorr - opd[0]


    return amp_means, tshift, opdstart_tcor, bestfitamp,bestfitamp_highpoly
@numba.jit()
def gaussian(x, mu, sig, amp):
    return amp * np.exp(-0.5 * (x-mu)**2 / sig**2)

fgx = np.linspace(-64,64,128)
@numba.jit()
def gaussian_msub(x, mu, sig, amp):
    gausmod = amp * np.exp(-0.5 * (x - mu) ** 2 / sig ** 2)
    fullgmod = amp * np.exp(-0.5 * (fgx - mu) ** 2 / sig ** 2)
    return gausmod - fullgmod.mean()

@numba.jit()
def g2_sig_surface(g2width, g2amp, g2position, g2shape):
    g2frame = np.zeros(g2shape)
    timechunks = g2shape[0]

    timedelsaysize = g2shape[1]
    x = np.arange(timedelsaysize)
    for i in range(timechunks):
        g2frame[i] = g2frame[i] + gaussian(x, g2position[i], g2width, g2amp[i])
    return g2frame

@numba.jit()
def g2_sig_surface_gsub(g2width, g2amp, g2position, g2shape):
    g2frame = np.zeros(g2shape)
    timechunks = g2shape[0]

    timedelsaysize = g2shape[1]
    x = np.arange(timedelsaysize)
    for i in range(timechunks):
        g2frame[i] = g2frame[i] + gaussian_msub(x, g2position[i], g2width, g2amp[i])
    return g2frame

def fourier(x, *a):
    tau=a[0]
    phi=a[1]
    ret = a[2] * np.cos(tau * x + phi)
    for deg in range(3, len(a)-1):
        ret += a[deg] * np.cos(tau*(deg+1) * 2 * x + phi)
    return ret
# @numba.jit()

def sigmoid(x,fcut,sigsig):
  return 1 / (1 + np.exp(-(x-fcut)/sigsig))

def fourier_radio_clean(g2data, p0=[2,1]):
    timd = np.arange(g2data.shape[1])
    cleanframe = np.zeros(g2data.shape)
    cleanpars = np.zeros((g2data.shape[0],3))
    for i,g2sig in enumerate(g2data):
        meansubg2 = g2sig - g2sig.mean()
        popt, pcov = curve_fit(fourier, timd, meansubg2, p0=p0 + 1 * [np.ptp(g2sig)], maxfev=10000)
        formod = fourier(timd, *popt)
        meanfoursub = meansubg2 -formod
        cleanframe[i] = meanfoursub - meanfoursub.mean()
        cleanpars[i] = popt
    return cleanframe, cleanpars
def radio_clean_sigmoid(dat, freqcut=65, sigsig=2):

    radioindexfft = np.array([np.fft.fft(g - g.mean()) for g in dat])
    nfreq = len((radioindexfft.mean(axis=0)))
    freq = (np.arange(nfreq) - nfreq / 2) * 250e6 / (nfreq * 1e6)

    gauwin = (1-sigmoid(freq,freqcut,sigsig))*(1-sigmoid(-freq,freqcut,sigsig))
    gauwin = np.fft.fftshift(gauwin)
    cleang2 = np.array([np.fft.ifft(g * gauwin) for g in radioindexfft]).real
    return cleang2, radioindexfft, np.fft.fftshift(freq),gauwin

def g2_shifter(g2_surface, opd):
    meanopd = np.array(opd - opd.mean())
    minopd = meanopd - meanopd.max()
    length, width = g2_surface.shape
    shiftsig = np.zeros((length, width))
    x = np.arange(width, dtype=int)
    for i in range(0, length):
        xg =np.array(np.ceil(x + minopd[i]), dtype=int)
        shiftsig[i] = g2_surface[i][xg]
    return shiftsig

def g2_shifter_mid(g2_surface, opd,cut=None):
    length, width = g2_surface.shape
    shiftsig = np.zeros((length, width))
    roundopd = -np.array(np.ceil((opd-64)), dtype=int)
    for i in range(0, length):
        shiftsig[i] = np.roll(g2_surface[i], roundopd[i])
    if cut:
        midopd = (roundopd+opd).mean()
        midind = int(np.ceil(midopd))
        lowind = midind-cut
        highind = midind+cut
        shiftsig = shiftsig[:,lowind:highind]
        newopd = roundopd+opd - midind + cut
    else: newopd = roundopd+opd
    return shiftsig,newopd

def g2_amps_rbr(g2surf, opd):
    gamps = []
    tim = np.arange(len(g2surf[0]))
    for i, rw in enumerate(g2surf):
        fitg, fiterr = curve_fit(gaussian, tim, rw, [opd[i], .85, .2],
                                 bounds=[[(opd[i]) * .999, .85, -10],
                                         [(opd[i]) * 1.0001, .8501, 10]])
        gamps.append(fitg[-1])
    gamps = np.array(gamps)
    return gamps

def amp_anal_airy_limb(data, odp_corr, baseline, start, end, guess=[120,1,0.3,0.85], bounds=[[60,.4, 0.3,0.84],[400,1.2, 0.301,0.86]]):
    cut_opd_correction = odp_corr - start
    cutdata = data[:, start:end]
    g2shape = cutdata.shape
    def g2_sig_amp_ravel(x, *args):
        airyarg = [term for term in args[:-1]]
        g2amp_model = IImodels.airynormLimb(baseline, *airyarg)
        g2amp_model = g2amp_model
        g2mod = g2_sig_surface(args[-1], g2amp_model, cut_opd_correction, g2shape)
        ravelg2 = (g2mod - g2mod.mean(axis=1)[:,None]).ravel()
        return ravelg2
    g2fitpar, g2fiterr = curve_fit(f=g2_sig_amp_ravel,
                                   xdata=baseline,
                                   ydata=cutdata.ravel(),
                                   p0=guess,
                                   bounds=bounds)
    amps = IImodels.airynormLimb(baseline, *g2fitpar[:-1])
    return amps, g2fitpar, g2fiterr

def g2_opd_fitter(data, g2width, odp_corr, baseline, order=3):

    cutdata = data
    g2shape = cutdata.shape
    cut_opd_correction = odp_corr
    def g2_sig_amp_ravel(x, *args):
        polyterms = [term for term in args]
        g2amp_poly = np.poly1d(polyterms[1:])
        g2amp_model = g2amp_poly(x)
        opd_off=args[0]
        ravelg2 = g2_sig_surface(g2width, g2amp_model, cut_opd_correction+opd_off, g2shape).ravel()
        return ravelg2

    time = np.arange(cutdata.shape[0])

    guess_par = np.zeros(order+1)
    guess_par[0] = 0
    g2fitpar, g2fiterr = curve_fit(f=g2_sig_amp_ravel,
                                   xdata=baseline,
                                   ydata=cutdata.ravel(),
                                   p0=guess_par,
                                   maxfev=5000)
    amps = np.poly1d(g2fitpar[1:])(baseline)
    return amps, cut_opd_correction, cutdata, g2fitpar