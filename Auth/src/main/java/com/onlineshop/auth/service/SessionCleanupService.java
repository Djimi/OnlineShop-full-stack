package com.onlineshop.auth.service;

import com.onlineshop.auth.repository.SessionRepository;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.scheduling.annotation.Scheduled;
import org.springframework.stereotype.Service;
import org.springframework.transaction.annotation.Transactional;

import java.time.Clock;

@Service
@Slf4j
public class SessionCleanupService {

    private final SessionRepository sessionRepository;
    private final Clock clock;

    public SessionCleanupService(SessionRepository sessionRepository, Clock clock) {
        this.sessionRepository = sessionRepository;
        this.clock = clock;
    }

    @Scheduled(fixedDelayString = "${session.cleanup.interval-ms:3600000}")
    @Transactional
    public void cleanupExpiredSessions() {
        sessionRepository.deleteExpiredSessions(clock.instant());
        log.debug("Expired session cleanup completed");
    }
}