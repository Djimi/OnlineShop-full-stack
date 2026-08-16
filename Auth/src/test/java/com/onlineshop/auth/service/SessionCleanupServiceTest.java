package com.onlineshop.auth.service;

import com.onlineshop.auth.repository.SessionRepository;
import org.junit.jupiter.api.BeforeEach;
import org.junit.jupiter.api.Test;
import org.junit.jupiter.api.extension.ExtendWith;
import org.mockito.Mock;
import org.mockito.junit.jupiter.MockitoExtension;

import java.time.Clock;
import java.time.Instant;
import java.time.ZoneId;

import static org.mockito.Mockito.verify;

@ExtendWith(MockitoExtension.class)
class SessionCleanupServiceTest {

    private static final Instant FIXED_TIME = Instant.parse("2025-01-15T10:00:00Z");

    @Mock
    private SessionRepository sessionRepository;

    private Clock fixedClock;
    private SessionCleanupService sessionCleanupService;

    @BeforeEach
    void setUp() {
        fixedClock = Clock.fixed(FIXED_TIME, ZoneId.of("UTC"));
        sessionCleanupService = new SessionCleanupService(sessionRepository, fixedClock);
    }

    @Test
    void cleanupExpiredSessions_deletesSessionsExpiredBeforeNow() {
        sessionCleanupService.cleanupExpiredSessions();

        verify(sessionRepository).deleteExpiredSessions(FIXED_TIME);
    }
}