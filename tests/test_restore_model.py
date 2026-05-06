from mailfallback.models import JobStatus, RestoreJob, RestoreMode


def test_restore_job_defaults(db_session, default_store):
    from mailfallback.models import Account, User

    user = User(username="restoreuser", password_hash="x", store_id=default_store.id)
    db_session.add(user)
    db_session.flush()

    src = Account(
        name="src",
        imap_host="imap.src.com",
        imap_port=993,
        maildir_path="/data/mailboxes/src-uuid",
        store_id=default_store.id,
    )
    tgt = Account(
        name="tgt",
        imap_host="imap.tgt.com",
        imap_port=993,
        maildir_path="/data/mailboxes/tgt-uuid",
        store_id=default_store.id,
    )
    db_session.add_all([src, tgt])
    db_session.flush()

    job = RestoreJob(
        source_account_id=src.id,
        target_account_id=tgt.id,
        restore_mode=RestoreMode.full,
        requested_by=user.id,
    )
    db_session.add(job)
    db_session.commit()
    db_session.refresh(job)

    assert job.id is not None
    assert job.status == JobStatus.pending
    assert job.restore_mode == RestoreMode.full
    assert job.folder_mapping == "original"
    assert job.skip_duplicates is True
    assert job.total_messages == 0
    assert job.restored_messages == 0
    assert job.skipped_messages == 0
    assert job.failed_messages == 0
    assert job.error is None
    assert job.selected_folders is None
    assert job.selected_uids is None
    assert job.requested_at is not None


def test_restore_mode_enum():
    assert RestoreMode.full == "full"
    assert RestoreMode.folder == "folder"
    assert RestoreMode.selection == "selection"
