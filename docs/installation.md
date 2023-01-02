Installation of the required GIS software environment for calculating the models can be done locally, using `conda` or `mamba` package managers, or with Docker, or by using a `makefile`

## :simple-anaconda: Conda

Use the [`conda`](https://docs.conda.io/en/latest/){target=_blank} package manager. 

We recommend installing `mamba` and then installing the `environment.yml` for the geospatial environment

```
conda install -c conda-forge mamba -y

mamba env create eemt -f environment.yml
```

```
conda activate eemt
```

## :simple-docker: Docker

Our cached Docker Image is maintained on this GitHub repository, and uses an Action to build and cache the container on CyVerse Harbor.

```
docker pull harbor.cyverse.org/vice/xpra/qgis:22.04
```

```
docker run -it --rm -p 9876:9876 harbor.cyverse.org/vice/xpra/qgis:22.04
```

The container will start and want to run in your browser. It supports a full desktop environment with QGIS, GRASS, and GDAL pre-installed. 

## Compiling GRASS from scratch

![osgeo](https://www.osgeo.org/wp-content/themes/roots/assets/img/logo-osgeo.svg){width="300"}

![grassgis](https://www.osgeo.org/wp-content/uploads/grassgis_logo_colorlogo_text_whitebg_square-250x250.png){width="300"}

https://grasswiki.osgeo.org/wiki/Compile_and_Install

https://github.com/OSGeo/grass/blob/main/INSTALL.md 

Installation order for building GRASS-GIS must be done sequentially:

1. PROJ
2. GDAL-OGR (compiled without GRASS support)
3. PostgreSQL, MySQL, sqlite (optional)
4. GRASS GIS
5. GDAL-OGR-GRASS plugin (optional)

### Makeflow & WorkQueue installation

The [Cooperative Computing Lab (CCL)](http://ccl.cse.nd.edu/){target=_blank} at Notre Dame are the creators of `makeflow` and `workqueue` 

[![makeflow](http://ccl.cse.nd.edu/software/makeflow/MakeflowLogo3.png){width="300"}](http://ccl.cse.nd.edu/software/makeflow/){target=_blank}

[![workqueue](http://ccl.cse.nd.edu/software/workqueue/WorkQueueLogo2.png){width="300"}](http://ccl.cse.nd.edu/software/workqueue/){target=_blank}

We are using `conda` pre-installed packages for both makeflow and workqueue 