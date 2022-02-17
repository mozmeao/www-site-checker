# www-site-checker

This project comprises tooling designed to check on the state of the mozilla.org site.

_CURRENT STATUS: WIP, pre-production_

Current supported checks:

* Outbound URLs from www.mozilla.org - verified by domain and or an allow-list

Roadmap / future checks

* Support throttling, if necessary in real use
* Site-wide smoke test: confirmation of 200 OK from every single page (in dev/stage/prod)
* HTML validation of all CMS-authored pages


## Usage

### Automatic usage
Currently, checks are set to run when updated code in this repo is pushed or (TO BE RE-ENABLED) on a twice-a-day schedule. If any issues are spotted, alerts are sent to Sentry and Slack (if either is configured) and report files are uploaded as artifacts as a result of the Github Action (more on this below)

### Manual usage and local development
You can also clone this repo to your local machine and run it there. This is the recommended approach for developing new checks:

```
$ git clone git@github.com:mozmeao/www-site-checker.git
$ cd www-site-checker
$ python bin/run_checks.py --sitemap-url=https://www.mozilla.org/sitemap.xml
```
The above will start to work through the entire sitemap (and child sitemaps) at that URL

If you only want to check a smaller batch of URLs (handy in development), add the `--batch` param:
```
# Only inspect batch three of all URLs to check, after slicing the site into 40 batches
$ python bin/run_checks.py --sitemap-url=https://www.mozilla.org/sitemap.xml --batch=3:40
```

And if you only want to check specific pages, you use the `--specific-url` param, which can be repeated. The following, for example, checks the homepage and a Fx mobile downbload page
```
$ python bin/run_checks.py --specific-url=https://www.mozilla.org/ --specific-url=https://www.mozilla.org/en-US/firefox/browsers/mobile/
```

There is a default allowlist in use (`data/allowlist.yaml`) but an alernative can be passed via the `--allowlist` param

```
$ python bin/run_checks.py --sitemap-url=https://www.mozilla.org/sitemap.xml --allowlist=/path/to/custom/allowlist.yaml
```

If you want or need to check a site whose sitemap points to a _different_ domain (eg you want to check an origin server whose sitemap is hard-coded to refer to the CDN domain, or a localhost setup) you should ensure the server is listed as an option in the allowlist and also pass the `--maintain-hostname` parameter.

For example:

```
$ python bin/run_checks.py --sitemap-url=http://origin-server.example.com/sitemap.xml --maintain-hostname
```

or, for localhost

```
$ python bin/run_checks.py --sitemap-url=http://localhost:8000/sitemap.xml --maintain-hostname
```



If you want to test the Sentry integration locally, you can pass a Sentry DSN as an environment variable. Here, we're passing a URL to [Kent - a local 'fake Sentry'](https://github.com/willkg/kent)

```
$ SENTRY_DSN=http://public@127.0.0.1:8011/1 python bin/run_checks.py --sitemap-url=https://www.mozilla.org/sitemap.xml
```


## The output files
If unexpected URLs are detected, they are output in pairs:
* files starting `flat_` contain a flat list of the unexpected URLs
* files starting `nested_` show each unexpected URL followed by a tab-indented list of the pages/URLs that feature the unexpected URL.

If the checks were carried out in batches, there may be multiple pairs of output files, with the batch number included in the filename.

Running the checks locally will put files in the `output/` directory.
Checks run via Github Actions will gave a `scan-results` archive in the artifacts section for the relevant run, which can be downloaded and inspected.


## Adding to the default allowlist

If you come across an alert saying there was an unexpected URL detected and you're happy to allow it here's how you make an unexpected URL into an expected one:

### via Githu

* Browse to and edit the `data/allowlist.yaml` file\
* Add either a new entry to `allowed_outbound_url_literals` or a new _tested_ regex to `allowed_outbound_url_regexes`
* Raise a new PR against the `main` branch. Github Actions will run the site checks. If your new rule change is valid, the checks will no longer consider that URL to be unexpected

### Locally

* Make a new branch off `main`
* Edit `data/allowlist.yaml`
* Run the checks locally (see above)
* Push the branch up and raise a PR

## Development
### Linting, etc
Install [pre-commit](https://pre-commit.com/#install), and then run `pre-commit install` and you'll be setup
to auto format your code according to our style and check for errors for every commit.

### Tests
TO COME
