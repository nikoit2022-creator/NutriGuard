<div align="center">
<img width="1200" height="475" alt="GHBanner" src="https://ai.google.dev/static/site-assets/images/share-ais-513315318.png" />
</div>

# Run and deploy your AI Studio app

This contains everything you need to run your app locally.

View your app in AI Studio: https://ai.studio/apps/91df2d7b-047e-4d9b-8293-f6ef127a2ddc

## Run Locally

**Prerequisites:**  [Android Studio](https://developer.android.com/studio)


1. Open Android Studio
2. Select **Open** and choose the directory containing this project
3. Allow Android Studio to finish importing and syncing the project.
4. Create a file named `.env` in the project directory and set `GEMINI_API_KEY` in that file to your Gemini API key (see `.env.example` for an example)
5. Run the app on an emulator or physical device. Debug builds use Android's standard automatically generated debug keystore; no project-local keystore or Gradle edit is required.
6. If you have already published your app in AI Studio, please [request upload key reset](https://support.google.com/googleplay/android-developer/answer/9842756#zippy=%2Crequest-an-upload-key-reset) in Google Play Console.

## Private APK updates

Release builds check `GET /api/v1/app-updates/android/latest` when opened. If its
`versionCode` is newer, the app offers to download the APK, checks its size and
SHA-256, and opens Android's system installer. The user may need to allow
installation from NutriGuard once and confirm each update. Debug builds skip
this check because their debug signing certificate cannot update a release APK.

The first updater-enabled build is version `1.2` (`versionCode` 3). Install a
**signed release APK** once on each phone. Keep the same application ID and
signing key for every later release, and increment `versionCode` each time.
An existing Android Studio debug installation may require uninstalling first;
back up any local app data before that one-time transition.

The existing release Gradle configuration uses alias `upload`, `KEYSTORE_PATH`,
`STORE_PASSWORD`, and `KEY_PASSWORD`. Build with `./gradlew assembleRelease` in
`android-app/` after setting those variables. Keep the keystore and passwords
outside Git and retain a secure backup; losing the key prevents future APK
updates to installations signed with it.

After the backend update feed has been deployed, copy a signed APK to the
backend host and publish it with
`python scripts/publish_android_release.py APK VERSION_CODE VERSION_NAME` from
`nutriguard-backend/`. The script requires Android SDK `apksigner` and `aapt` on the host.
You can publish version 3 as the initial download; phones already on version 3
will begin receiving update prompts when version 4 is published.

## Backend connection

The Android client reads `BACKEND_BASE_URL` in this order:

1. Gradle property (`-PBACKEND_BASE_URL=...`)
2. Environment variable `BACKEND_BASE_URL`
3. Local ignored `local.properties`
4. Emulator fallback: `http://10.0.2.2:8000/`

For a physical phone on the same network as the backend, add the reachable backend URL to your local `local.properties` without committing it:

```properties
BACKEND_BASE_URL=http://YOUR_BACKEND_IP:8000/
```

The URL must end with `/`. Debug builds permit cleartext HTTP for local-network development; release builds keep the stricter network policy. Never commit machine-specific addresses or credentials.
