# LifeLine landing page

Public static site: https://lifeline-relay.pages.dev

Cloudflare Pages project: `lifeline-relay`, production branch: `main`.
Publish only `website/public/`. It contains no backend, runtime credentials,
personal test numbers, incident records, or model configuration.

Preview from the repository root:

```sh
python3 -m http.server 4173 --directory website/public
```

Redeploy using an authenticated Cloudflare CLI:

```sh
wrangler pages deploy website/public --project-name lifeline-relay --branch main
```

The public number is the assigned project line. The page states the verified-phone
trial limitation and prototype boundaries. Publishing the site does not change
carrier access or verify phone/GPU operation.

Forest photograph: [Unsplash](https://images.unsplash.com/photo-1441974231531-c6227db76b6e),
served locally as `forest.jpg`. Typefaces: DM Sans and Manrope via Google Fonts.
