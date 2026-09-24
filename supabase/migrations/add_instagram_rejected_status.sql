alter table public.instagram_posts
  drop constraint if exists instagram_posts_status_check;

alter table public.instagram_posts
  add constraint instagram_posts_status_check
  check (
    status = any (
      array[
        'pending',
        'manus_processing',
        'ready',
        'publishing',
        'published',
        'rejected',
        'error'
      ]::text[]
    )
  );
