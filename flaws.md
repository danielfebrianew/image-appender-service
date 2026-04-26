# Project Flaws

Dokumen ini mencatat kekurangan yang terlihat saat membaca codebase dan menulis unit test pytest. Status test saat dokumen dibuat: `52 passed`.

## High Priority

1. `PUT /api/projects/{project_id}` dapat menerima `cover` lengkap dari request, termasuk `path` arbitrer. Endpoint `GET /api/projects/{project_id}/cover` lalu mengirim `FileResponse(project.cover.path)`, sehingga path yang disuntikkan ke project bisa dipakai untuk mencoba menyajikan file lokal dari server. Lihat `app/main.py:226-227` dan `app/main.py:296-303`.

2. Path cover yang tersimpan juga dipakai untuk `unlink()` saat upload/delete cover. Jika project JSON atau payload update menyimpan path yang tidak semestinya, operasi cover berikutnya bisa menghapus file lokal yang bisa diakses proses server. Lihat `app/main.py:257-260` dan `app/main.py:287-290`.

3. Endpoint register video menerima path filesystem dari client dan menyalinnya ke storage server. Ini cocok untuk tool lokal tepercaya, tetapi berbahaya jika service pernah diekspos karena client bisa membuat server membaca file lokal mana pun yang readable. Validasi saat ini hanya mengecek path exists. Lihat `app/main.py:90-95` dan `app/models.py:103-110`.

4. Tidak ada autentikasi atau otorisasi. Semua endpoint mutasi, upload, path registration, render, dan download terbuka selama request bisa mencapai service. CORS dibatasi origin, tetapi itu bukan auth boundary. Lihat `app/main.py:56-64`.

5. Storage JSON tidak punya locking antar request/proses. Pola read-append-write di endpoint upload bisa kehilangan update saat request paralel, dan `write_json()` memakai satu file `.tmp` tetap sehingga concurrent write bisa saling timpa. Lihat `app/storage.py:34-39`, `app/main.py:105-107`, dan `app/main.py:153-155`.

6. Render job state murni in-memory. Restart server menghilangkan status job, multi-worker tidak berbagi state, dan download/status job lama akan hilang walaupun output file masih ada. Lihat `app/jobs.py:11-17`, `app/jobs.py:97`, dan `app/main.py:326-341`.

## Medium Priority

1. `CONTEXTCLIPPER_MAX_RENDER_CONCURRENT` didefinisikan tetapi tidak dipakai. Render baru selalu dibuat dan dijalankan via `BackgroundTasks` tanpa semaphore/queue concurrency, sehingga beberapa render besar bisa berjalan bersamaan. Lihat `app/config.py:15` dan `app/main.py:314-323`.

2. `Project.model_copy(update=...)` dipakai tanpa validasi ulang. Karena `UpdateProjectRequest` mengizinkan field optional bernilai `null`, request seperti `"layout": null`, `"click_sound": null`, atau `"name": null` bisa menghasilkan `Project` invalid yang baru gagal di runtime. Lihat `app/main.py:216-227` dan `app/main.py:241`.

3. Preview hardcode overlay area ke 30% tinggi video, sementara render final memakai `project.layout.image_area_ratio`. Akibatnya preview bisa berbeda dari output render saat layout project diubah. Lihat `app/media.py:109-121` dan `app/render.py:176-179`.

4. Progress render tidak mengakumulasi offset per segment. Setiap `run_ffmpeg()` menghitung progress berdasarkan bobot segment saja, lalu menimpa progress global; progress bisa turun saat masuk segment berikutnya. Lihat `app/render.py:140-145`, `app/render.py:161-169`, dan `app/render.py:253`.

5. `concat_with_reencode()` mengasumsikan setiap input punya stream audio `[i:a:0]`. Video tanpa audio, cover/source tertentu, atau segment yang tidak membawa audio bisa membuat concat gagal. Lihat `app/render.py:64-86`.

6. Saat click sound aktif, filter audio memakai `[0:a]` secara wajib. Video sumber tanpa audio akan gagal walaupun mapping non-click memakai `0:a?`. Lihat `app/render.py:221-240`.

7. Click sound dipasang di awal setiap overlay segment, bukan tepat di awal setiap track. Jika overlap memecah track panjang menjadi beberapa segment, click bisa berbunyi di batas segment tambahan. Lihat `app/render.py:214-240` dan `app/render.py:306-319`.

8. Concatenation non-cover memakai `-c copy` untuk menyambung segment copy dan segment overlay. Karena segment overlay dire-encode dan segment copy mempertahankan codec/parameter asli, concat bisa gagal atau menghasilkan output bermasalah jika parameter stream tidak identik. Lihat `app/render.py:338-351`.

9. `run_render_job()` mempercayai exit code ffmpeg dan menandai job `done` tanpa memastikan output file benar-benar ada dan tidak kosong. Endpoint download baru mendeteksi masalahnya belakangan sebagai `output not ready`. Lihat `app/render.py:283-304`, `app/render.py:356-357`, dan `app/main.py:334-341`.

10. `run_ffprobe()` mengasumsikan output JSON selalu punya `streams[0]`, `width`, `height`, dan nilai numerik. Output ffprobe yang valid tetapi tidak sesuai akan menjadi exception 500 tidak tertangani, bukan 400 dengan pesan jelas. Lihat `app/media.py:42-51`.

11. Upload file tidak punya batas ukuran, MIME/content-type validation, atau whitelist suffix. Gambar memang diverifikasi setelah ditulis, tetapi video dan registered file bisa memenuhi disk lebih dulu. Lihat `app/storage.py:101-108`, `app/main.py:77-108`, dan `app/main.py:127-156`.

12. `save_base64_image()` memakai `base64.b64decode()` tanpa `validate=True`, sehingga sebagian input non-base64 bisa diterima sebagai bytes. Endpoint image/cover biasanya menangkapnya lewat `inspect_image()`, tetapi helper ini sendiri tidak strict. Lihat `app/media.py:77-89`.

13. Soft delete image tidak mengecek apakah image masih dipakai project track. Project yang sudah menyimpan track ke image tersebut akan gagal render/preview setelah image dihapus. Lihat `app/main.py:170-178` dan `app/render.py:367-377`.

14. Deteksi overlap di `validate_project_assets()` memakai urutan track mentah, bukan urutan start time yang dipakai render. Warning overlap bisa miss atau muncul keliru jika tracks datang tidak terurut. Lihat `app/render.py:367-377`.

15. Mutasi job selain `create()` tidak memakai lock. Update progress/log/status dari beberapa coroutine atau websocket broadcast bisa race pada object `JobState` yang sama. Lihat `app/jobs.py:26-71`.

## Low Priority / Maintenance

1. Default config untuk click sound adalah `assets/click_default.mp3`, tetapi file repo yang ada adalah `assets/click.mp3` dan `.env.example` mengarah ke `assets/click.mp3`. Tanpa `.env`, click sound diam-diam nonaktif. README juga menyebut warning, tetapi code hanya skip tanpa log. Lihat `app/config.py:16`, `.env.example:6`, `README.md:89`, dan `app/render.py:183-184`.

2. Repo berisi runtime artifact besar: `data/` sekitar 1.2 GB, `image-appender-env/` sekitar 220 MB, dan `__pycache__`. Root `.gitignore` tidak ada, hanya `.gitignore` di virtualenv. Ini membuat project berat, sulit direview, dan rawan commit file generated.

3. `pyproject.toml` belum mendeklarasikan runtime dependencies, sementara `requirements.txt` sekarang mencampur dependency runtime dan pytest. Lebih rapi jika ada dependency metadata/lockfile dan dependency dev terpisah. Lihat `pyproject.toml:1-14` dan `requirements.txt:1-9`.

4. Tidak ada CI, coverage config, formatter config selain ruff line length, atau smoke test ffmpeg sungguhan dengan sample media kecil. Unit test sudah ada, tetapi render pipeline end-to-end masih belum dibuktikan.

5. `normalize_tracks()` punya variable `last_end` yang di-update tetapi tidak dipakai. Ini sinyal sisa implementasi overlap handling yang belum selesai. Lihat `app/render.py:23-35`.

6. Error handling JSON storage masih mentah. File JSON korup akan melempar exception langsung saat list/get, tanpa recovery, backup, atau pesan API yang ramah. Lihat `app/storage.py:27-31` dan `app/storage.py:42-47`.
