## This small script tries to replicate the ReverseGeocoding endpoint of [Photon](https://github.com/komoot/photon).



### Excerpt from the Photon Docs

Request ([LINK](https://github.com/komoot/photon/blob/master/docs/api-v1.md#reverse))


> ```http://localhost:2322/reverse?lon=10&lat=52&radius=10```
>
> The mandatory lat and lon parameters describe the coordinate which to look up the location description for. The optional radius parameter can be used to specify a  value in kilometers to reverse geocode within. The value has to be between 0 and > 5000 km.
>
> The /reverse call can be customized with the common parameters.



## Response ([LINK](https://github.com/komoot/photon/blob/master/docs/api-v1.md#results-for-search-and-reverse))

> Results for Search and Reverse
> photon returns a response in GeoJSON format. The properties returned follow the specification of the GeocodeJson format with the following extra fields added:
>
>    extra is an object containing any extra tags, if available.
>
>Example response:
>
> ```json
>{
>  "features": [
>    {
>      "properties": {
>        "name": "Berlin",
>        "state": "Berlin",
>        "country": "Germany",
>        "countrycode": "DE",
>        "osm_key": "place",
>        "osm_value": "city",
>        "osm_type": "N",
>        "osm_id": 240109189
>      },
>      "type": "Feature",
>      "geometry": {
>        "type": "Point",
>        "coordinates": [13.3888599, 52.5170365]
>      }
>    },
>    {
>      "properties": {
>        "name": "Berlin Olympic Stadium",
>        "street": "Olympischer Platz",
>        "housenumber": "3",
>        "postcode": "14053",
>        "state": "Berlin",
>        "country": "Germany",
>        "countrycode": "DE",
>        "osm_key": "leisure",
>        "osm_value": "stadium",
>        "osm_type": "W",
>        "osm_id": 38862723,
>        "extent": [13.23727, 52.5157151, 13.241757, 52.5135972]
>      },
>      "type": "Feature",
>      "geometry": {
>        "type": "Point",
>        "coordinates": [13.239514674078611, 52.51467945]
>      }
>    }
>  ]
>}
>```

As per intend of this Project we will only return a limited subset of this data.
With focus on only `country`and `state

## This will the be setup in Dawarich like this ([Docs](https://dawarich.app/docs/self-hosting/configuration/reverse-geocoding/))

``` yaml
networks:
  dawarich:
services:
  dawarich_app:
    image: freikin/dawarich:latest
    ...
    environment:
      RAILS_ENV: production
      ...
      APPLICATION_PROTOCOL: http
      PHOTON_API_HOST: photon.yourdomain.com
      PHOTON_API_KEY: your_photon_api_key # If you're using Photon API instance for Patreon supporters
      PHOTON_API_USE_HTTPS: true # or false if you want to use HTTP instead of HTTPS
    logging:
    ...
  dawarich_sidekiq:
    image: freikin/dawarich:latest
    ...
    environment:
      RAILS_ENV: production
      ...
      APPLICATION_PROTOCOL: http
      PHOTON_API_HOST: photon.yourdomain.com
      PHOTON_API_KEY: your_photon_api_key # If you're using Photon API instance for Patreon supporters
      PHOTON_API_USE_HTTPS: true # or false if you want to use HTTP instead of HTTPS
    logging:
    ...
```



## Example response from chibigeo.com

```json
{
  "type": "FeatureCollection",
  "features": [
    {
      "type": "Feature",
      "properties": {
        "osm_type": "W",
        "osm_id": 518071791,
        "osm_key": "tourism",
        "osm_value": "attraction",
        "name": "Brandenburger Tor",
        "housenumber": "1",
        "street": "Pariser Platz",
        "district": "Mitte",
        "city": "Berlin",
        "postcode": "10117",
        "country": "Deutschland",
        "countrycode": "DE"
      },
      "geometry": {
        "type": "Point",
        "coordinates": [
          13.3777034,
          52.5162699
        ]
      }
    }
  ]
}

```