"""
This module contains container objects that corresponds to a dataset
and image in the database The correspondence is matched through the
private _dsid and _imageid attributes.

Each dataset contains several database Images; database Images
correspond to the images table in the databse, *not* to sourcefinder
images or actual image data files on disk (this distinction is
important; while there are certainly parts in common, several are
not). This is in contrast to earlier versions of this module, where a
sourcefinder could basically not exist without a database.

The current setup is done in large part to keep the database and
sourcefinder (and other parts of the TKP package) separate; tightly
integrated database tables/sourcefinder images/data files make it more
difficult to improve the code or distribute parts separately. While
not tested, it seems unlikely this will have a noticable influence on
the functioning of the actual TKP pipeline (TRAP).


Usage
=====

In practice, a DataSet object is created, and separate Images are
created referencing that DataSet() instance; ids are automatically
assigned where necessary.

Objects can also be created using an existing id; data is then taken
from the database.


>>> database = tkp.database.database.DataBase()
# Each object type takes a data dictionary, which for newly objects
# has some required keys (& values). For a DataSet, this is only 'dsinname';
# for an Image, the keys are 'freq_eff', 'freq_bw_', 'taustart_ts',
# 'tau_time' & 'url'
# database holds the connection to the database
>>> dataset = DataSet(data={'dsinname': 'a dataset'}, database=database)
# Here, dataset indirectly holds the database connection
>>> image1 = Image(data={'freq_eff': '80e6', 'freq_bw': 1e6,
    'taustart_ts': datetime(2011, 5, 1, 0, 0, 0), 'tau_time': 1800., 'url': '/'},
    dataset=dataset)  # initialize with defaults
>>> image1.tau_time
1800.
>>> image1.taustart_ts
datetime.datetime(2011, 5, 1, 0, 0, 0)
>>> image2 = Image(data={'freq_eff': '80e6', 'freq_bw': 1e6,
    'taustart_ts': datetime(2011, 5, 1, 0, 1, 0), 'tau_time': 1500., 'url': '/'},
    dataset=dataset)
>>> image2.tau_time
1500
>>> image2.taustart_ts
datetime.datetime(2011, 5, 1, 0, 1, 0)
# Images created with a dataset object, are automatically added to that dataset:
>>> dataset.images
[<tkp.database.dataset.Image object at 0x10151ce10>,
 <tkp.database.dataset.Image object at 0x10151cc90>]

To update objects, use the update() method.
This method works 2 ways: it (firstly) updates from the database to the object
(so if there have been changes in the database, the object will reflect that after
an update()); it (secondly) updates the object (and the database) with values
supplied by the user. The latter values are optional.


>>> image2.update(tau_time=2500)    # updates the database as well
>>> image2.tau_time
2500
>>> database.cursor.execute("SELECT tau_time FROM images WHERE imageid=%s" %
                             (image2.imageid,))
>>> database.cursors.fetchone()[0]
2500
>>> database.cursor.execute("UPDATE images SET tau_time=2000.0 imageid=%s" %
                             (image2.imageid,))
>>> image2.tau_time   # not updated yet!
2500
>>> image2.update()
>>> image2.tau_time
2000


It is also possible to create a DataSet or Image instance from the
database, using the ``id`` in the initializer:

>>> dataset2 = DataSet(id=dataset.id, database=database)
>>> image3 = Image(imageid=image2.imageid, database=database)
>>> image3.tau_time
2000

If an ``id`` is supplied, ``data`` is ignored.

"""

from __future__ import with_statement
import datetime
import logging
import utils as dbu
import monetdb.sql as db
from ..config import config
from .database import ENGINE


DERUITER_R = config['source_association']['deruiter_radius']


class DBObject(object):
    """Generic mini-ORM object

    Derived objects will need to implement __init__, which for
    practical reasons is split up in __init__ and _init_data: the
    latter is called at the end __init__, so a derived __init__ would
    have super(Derived, self).__init__() at the start and
    super(Derived, self)._init_data() at the end.
    """

    def __init__(self, data=None, database=None, id=None):
        """Basic initialization.

        Inherited classes will implement any actual database action,
        by calling self._init_data() at the end of their __init__
        method.
        """
        self._id = id
        self._data = {} if data is None else data.copy()
        self.database = database

    def _init_data(self):
        """Set up the data, either by creating a new DBOject or
        updating it from the database using the id

        This method should only be called from __init__(), probably at the end.

        Note that this does prevent proper (multi) inheritance,
        because it would get called several times then.
        """
        if self._id is not None:
            self.update()
        else:
            for key in self.REQUIRED:
                if key not in self._data:
                    raise AttributeError("missing required data key: %s" % key)
            self.id

    def __getattr__(self, name):
        """Obtain the 'name' attribute, where 'name' is a database column name"""
        # Get here when 'name' is not found as attribute
        # That likely means it is stored in self._data
        try:
            return self._data[name]
        except KeyError:
            raise AttributeError("attribute '%s' not found" % name)
        
    @property
    def id(self):
        """Add or obtain an id to/from the table

        The id is generated if self._id does not exist, effectively
        creating a new row in the database.
        """

        if self._id is None:
            query = ("INSERT INTO " + self.TABLE + " (" +
                     ", ".join(self._data.iterkeys()) + ") VALUES (" +
                     ", ".join(["%s"] * len(self._data)) + ")")
            if ENGINE == 'postgresql':
                query += " RETURNING " + self.ID
            values = tuple(self._data.itervalues())
            cursor = self.database.cursor
            try:
                # Insert a default source
                cursor.execute(query, values)
                if not self.database.autocommit:
                    self.database.connection.commit()
                self._id = cursor.lastrowid
                if ENGINE == 'postgresql':
                    self._id = cursor.fetchone()[0]
            except self.database.Error:
                logging.warn("insertion into database failed: %s",
                             (query % values))
                raise
        return self._id

    def update(self, **kwargs):
        """Update attributes from database, and set database values to
        kwargs when provided

        This method performs two functions, the first always and the
        second optionally:

            - it updates the attributes from the database. That is, it
              makes sure the Python instance is synchronized with the
              database.

            - (optional): it sets the column values in the database to
              the values provided through kwargs, for the associated
              database row. Attributes for the instance are of course
              also set to these values. Any kwargs that do not
              correspond to a column name are simply ignored.

        This function therefore first updates the instance from the
        database, and then optionally the database from the instance
        (with the provided keyword arguments).
        """

        self._sync_with_database()
        self._set_data(**kwargs)
            
    def _sync_with_database(self):
        """Update object attributes from the database"""
        results = dbu.columns_from_table(
            self.database.connection, self.TABLE, keywords=None,
            where={self.ID: self._id})
        # Shallow copy, but that's ok: all database values are
        # immutable (including datetime objects)
        self._data = results[0].copy()

    def _set_data(self, **kwargs):
        """Update the database with the supplied **kwargs.

        Supplied keywords that do not exist in the database will lead
        to a database error.
        """

        if not kwargs:
            return
        dbu.set_columns_for_table(self.database.connection, self.TABLE,
                                  data=kwargs, where={self.ID: self._id})
        self._data.update(kwargs)
        

class DataSet(DBObject):
    """Class corresponding to the dataset table in the database"""

    TABLE = 'datasets'
    ID = 'dsid'
    REQUIRED = ('dsinname',)
    
    def __init__(self, data=None, database=None, id=None):
        """If id is supplied, the data and image arguments are ignored."""
        super(DataSet, self).__init__(
            data=data, database=database, id=id)
        self.images = set()
        if not self.database:
            raise ValueError(
                "can't create DataSet object without a DataBase() object")
        self._init_data()

    def __str__(self):
        return 'DataSet: "%s". Database ID: %s %d images.' % (
            self.name, str(self._dsid), len(self.images))

    # Inserting datasets is handled a little different than normal inserts
    @property
    def id(self):
        """Add or obtain an id to/from the table"""

        if self._id is None:
            try:
                self._id = dbu.insert_dataset(self.database.connection,
                                              self._data['dsinname'])
            except self.database.Error, e:
                logging.warn("insertion of DataSet() into the database failed")
                raise
        return self._id

    # Get all images from database; implemented separately from update(),
    # since normally this would be too much overhead
    def update_images(self):
        """Renew the set of images by getting the images for this
        dataset from the database"""

        query = "SELECT imageid FROM images WHERE ds_id = %s"
        try:
            self.database.cursor.execute(query, (self._id,))
            results = self.database.cursor.fetchall()
        except db.Error, e:
            query = query % self._id
            logging.warn("database failed on query: %s", query)
            raise
        images = set()
        for result in results:
            images.add(Image(database=self.database, id=result[0]))
        self.images = images
                           
    # TO DO: Verify constants
    def detect_variables(self,  V_lim=0.2, eta_lim=3.):
        """Search through the whole dataset for variable sources"""

        return dbu.detect_variable_sources(
            self.database.connection, self._id, V_lim, eta_lim)


class Image(DBObject):
    """Class corresponding to the images table in the database"""

    TABLE = 'images'
    ID = 'imageid'
    REQUIRED = ('ds_id', 'tau_time', 'freq_eff', 'freq_bw', 'taustart_ts')
    
    def __init__(self, data=None, dataset=None, database=None, id=None):
        """If id is supplied, the data and image arguments are ignored."""
        super(Image, self).__init__(
            data=data, database=database, id=id)
        # Special part to deal when a DataSet() is supplied
        self.dataset = dataset
        if self.dataset:
            if self.dataset.database and not self.database:
                self.database = self.dataset.database
            self.dataset.images.add(self)
            self._data.setdefault('ds_id', self.dataset.id)
        self.sources = set()
        if not self.database:
            raise ValueError(
                "can't create Image object without a DataBase() object")
        self._init_data()

    # Inserting images is handled a little different than normal inserts
    @property
    def id(self):
        """Add or obtain an id to/from the table"""

        if self._id is None:
            try:
                # Insert a default image
                self._id = dbu.insert_image(
                    self.database.connection, self.dataset.id,
                    self._data['freq_eff'], self._data['freq_bw'],
                    self._data['taustart_ts'], self._data['url']
                )
            except self.database.Error, e:
                logging.warn("insertion of Image() into the database failed")
                raise
        return self._id

    # Get all sources from database; implemented separately from update(),
    # since normally this would be too much overhead
    def update_sources(self):
        """Renew the set of sources by getting the sources for this
        image from the database

        This method is separately implemented, because it's not always necessary
        and potentially (for an image with dozens or more sources) time & memeory
        consuming. 
        """

        query = "SELECT xtrsrcid FROM extractedsources WHERE image_id = %s"
        try:
            self.database.cursor.execute(query, (self._id,))
            results = self.database.cursor.fetchall()
        except db.Error, e:
            query = query % self._id
            logging.warn("database failed on query: %s", query)
            raise
        sources = set()
        for result in results:
            sources.add(ExtractedSource(database=self.database, id=result[0]))
        self.sources = sources

    def insert_extracted_sources(self, results):
        """Insert a list of sources

        Args:

            results (list): list of
                utility.containers.ExtractionResult objects (as
                returned from
                sourcefinder.image.ImageData().extract()), or a list
                of data tuples with the source information (ra, dec,
                ra_err, dec_err, peak, peak_err, flux, flux_err,
                det_sigma).
       """
        dbu.insert_extracted_sources(
            self.database.connection, self._id, results=results)
        
    def associate_extracted_sources(self, deRuiter_r=DERUITER_R):
        """Associate sources from the last images with previously
        extracted sources within the same dataset

        Args:

            deRuiter_r (float): The De Ruiter radius for source
                association. The default value is set through the
                tkp.config module
        """
        dbu.associate_extracted_sources(
            self.database.connection, self._id, deRuiter_r)

    def match_monitoringlist(self, update_image_column=True,
                             assoc_r=DERUITER_R, mindistance=30):
        """Match sources found in the current image with those in the
        monitoringlist"""
        
        image_id = self._id if update_image_column else -1
        dbu.match_runningcatalog_monitoringlist(
            self.database.connection, self.dataset.id, image_id,
            assoc_r=assoc_r, mindistance=mindistance)

    def monitoringsources(self, include_current=False):
        """Return a list of monitoring sources

        Kwargs:

            include_current (bool): should the method return sources
            that already have matched sources for this image?
        """

        exclude_image_id = None if include_current else self._id
        return dbu.list_monitoringsources(self.database.connection,
                                          dataset_id=self.ds_id,
                                          exclude_image_id=exclude_image_id)

    def insert_monitoring_sources(self, results):
        """Insert the list of measured monitoring sources for this image into
        extractedsources and runningcatalog

        Note that the insertion into runningcatalog can be done by
        xtrsrc_id from monitoringlist. In case it is negative, it is
        appended to runningcatalog, and xtrsrc_id is updated in the
        monitoringlist.
        """

        dbu.insert_monitoring_sources(self.database.connection, results, self._id)
        
        
class ExtractedSource(DBObject):
    """Class corresponding to the extractedsources table in the database"""

    TABLE = 'extractedsources'
    ID = 'xtrsrcid'
    REQUIRED = ('image_id', 'zone', 'ra', 'decl', 'ra_err', 'decl_err', 'x', 'y', 'z', 'det_sigma')

    def __init__(self, data=None, image=None, database=None, id=None):
        """If id is supplied, the data and image arguments are ignored."""
        super(ExtractedSource, self).__init__(
            data=data, database=database, id=id)
        # Special part to deal when an Image() is supplied
        self.image = image
        if self.image:
            if self.image.dataset.database and not self.database:
                self.database = self.image.dataset.database
            self.image.sources.add(self)
            self._data.setdefault('image_id', self.image.id)
        if not self.database:
            raise ValueError(
                "can't create ExtractedSource object without a DataBase() object")
        self._init_data()

    def lightcurve(self):
        """Obtain the complete light curve (within the current dataset
        for this source

        Returns:

            (list) list of 5-tuples, each tuple being:

                - observation start time as a datetime.datetime object

                - integration time (float)

                - peak flux (float)

                - peak flux error (float)

                - database ID of this particular source
        """

        return dbu.lightcurve(self.database.connection, self._id)
