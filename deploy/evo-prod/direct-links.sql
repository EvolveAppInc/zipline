BEGIN;
ALTER TABLE "User" ALTER COLUMN view SET DEFAULT
    '{"enabled":false,"disableTextFiles":true,"embed":false,"embedMediaOnly":false}'::jsonb;
UPDATE "User" SET view =
    '{"enabled":false,"disableTextFiles":true,"embed":false,"embedMediaOnly":false}'::jsonb
    WHERE view = '{}'::jsonb;
COMMIT;
