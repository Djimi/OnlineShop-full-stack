package com.onlineshop.auth.service;

import com.onlineshop.auth.dto.*;
import com.onlineshop.auth.entity.Session;
import com.onlineshop.auth.entity.User;
import com.onlineshop.auth.exception.InvalidUsernameOrPasswordException;
import com.onlineshop.auth.exception.UserAlreadyExistsException;
import com.onlineshop.auth.repository.SessionRepository;
import com.onlineshop.auth.repository.UserRepository;
import lombok.extern.slf4j.Slf4j;
import org.springframework.beans.factory.annotation.Value;
import org.springframework.security.crypto.password.PasswordEncoder;
import org.springframework.stereotype.Service;

import java.security.MessageDigest;
import java.security.NoSuchAlgorithmException;
import java.security.SecureRandom;
import java.time.Clock;
import java.time.Instant;
import java.util.HexFormat;

@Service
@Slf4j
public class AuthService {

    private final UserRepository userRepository;
    private final SessionRepository sessionRepository;
    private final PasswordEncoder passwordEncoder;
    private final SecureRandom secureRandom;
    private final Clock clock;
    private final long sessionExpirationSeconds;

    public AuthService(UserRepository userRepository, SessionRepository sessionRepository,
            PasswordEncoder passwordEncoder, SecureRandom secureRandom, Clock clock,
            @Value("${session.expiration:3600}") long sessionExpirationSeconds) {
        this.userRepository = userRepository;
        this.sessionRepository = sessionRepository;
        this.passwordEncoder = passwordEncoder;
        this.secureRandom = secureRandom;
        this.clock = clock;
        this.sessionExpirationSeconds = sessionExpirationSeconds;
    }

    public RegisterResponse register(RegisterRequest request) {
        long requestStartedAt = System.nanoTime();
        String normalizedUsername = request.getUsername().toLowerCase();
        long existsCheckStartedAt = System.nanoTime();
        boolean userExists = userRepository.existsByNormalizedUsername(normalizedUsername);
        log.info("Register operation db.existsByNormalizedUsername completed in {} ms",
                elapsedMillis(existsCheckStartedAt));
        if (userExists) {
            throw new UserAlreadyExistsException(request.getUsername());
        }

        long passwordHashStartedAt = System.nanoTime();
        String passwordHash = passwordEncoder.encode(request.getPassword());
        log.info("Register operation password hashing completed in {} ms",
                elapsedMillis(passwordHashStartedAt));

        User user = new User(request.getUsername(), passwordHash);
        long userSaveStartedAt = System.nanoTime();
        User savedUser = userRepository.save(user);
        log.info("Register operation db.save(user) completed in {} ms for userId={}",
                elapsedMillis(userSaveStartedAt), savedUser.getId());

        log.info("Register service completed in {} ms for userId={}",
                elapsedMillis(requestStartedAt), savedUser.getId());

        return RegisterResponse.builder()
                .userId(savedUser.getId())
                .username(savedUser.getUsername())
                .createdAt(savedUser.getCreatedAt())
                .updatedAt(savedUser.getUpdatedAt())
                .build();
    }

    public LoginResponse login(LoginRequest request) {
        long requestStartedAt = System.nanoTime();
        String normalizedUsername = request.getUsername().toLowerCase();

        long findUserStartedAt = System.nanoTime();
        User user = userRepository.findByNormalizedUsername(normalizedUsername)
                .orElseThrow(InvalidUsernameOrPasswordException::new);
        log.info("Login operation db.findByNormalizedUsername completed in {} ms for userId={}",
                elapsedMillis(findUserStartedAt), user.getId());

        long passwordMatchStartedAt = System.nanoTime();
        if (!passwordEncoder.matches(request.getPassword(), user.getPasswordHash())) {
            throw new InvalidUsernameOrPasswordException();
        }
        log.info("Login operation password verification completed in {} ms for userId={}",
                elapsedMillis(passwordMatchStartedAt), user.getId());

        long tokenGenerationStartedAt = System.nanoTime();
        String token = generateToken();
        log.info("Login operation token generation completed in {} ms for userId={}",
                elapsedMillis(tokenGenerationStartedAt), user.getId());

        String tokenHash = hashToken(token);
        Instant now = clock.instant();
        Instant expiresAt = now.plusSeconds(sessionExpirationSeconds);

        Session session = new Session();
        session.setTokenHash(tokenHash);
        session.setUser(user);
        session.setExpiresAt(expiresAt);

        long sessionSaveStartedAt = System.nanoTime();
        sessionRepository.save(session);
        log.info("Login operation db.save(session) completed in {} ms for userId={}",
                elapsedMillis(sessionSaveStartedAt), user.getId());

        log.info("Login service completed in {} ms for userId={}",
                elapsedMillis(requestStartedAt), user.getId());

        return LoginResponse.builder()
                .token(token)
                .userId(user.getId())
                .username(user.getUsername())
                .createdAt(session.getCreatedAt())
                .expiresAt(expiresAt)
                .tokenType("Bearer")
                .expiresIn(sessionExpirationSeconds)
                .build();
    }

    public ValidateResponse validateToken(String token) {
        long requestStartedAt = System.nanoTime();
        String tokenHash = hashToken(token);
        Instant now = clock.instant();

        long findSessionStartedAt = System.nanoTime();
        SessionRepository.SessionValidationProjection session = sessionRepository
                .findValidationProjectionByTokenHash(tokenHash)
                .orElse(null);
        log.info("Validate operation db.findValidationProjectionByTokenHash completed in {} ms",
                elapsedMillis(findSessionStartedAt));

        if (session == null
                || now.isBefore(session.getCreatedAt())
                || now.isAfter(session.getExpiresAt())) {
            log.info("Validate service completed in {} ms with valid=false", elapsedMillis(requestStartedAt));
            return ValidateResponse.builder()
                    .valid(false)
                    .build();
        }

        log.info("Validate service completed in {} ms with valid=true for userId={}",
                elapsedMillis(requestStartedAt), session.getUserId());

        return ValidateResponse.builder()
                .valid(true)
                .userId(session.getUserId())
                .username(session.getUsername())
                .createdAt(session.getCreatedAt())
                .expiresAt(session.getExpiresAt())
                .build();
    }

    private String generateToken() {
        byte[] bytes = new byte[32];
        secureRandom.nextBytes(bytes);
        return HexFormat.of().formatHex(bytes);
    }

    private String hashToken(String token) {
        long hashStartedAt = System.nanoTime();
        try {
            MessageDigest digest = MessageDigest.getInstance("SHA-256");
            byte[] hash = digest.digest(token.getBytes());
            log.info("Token hashing completed in {} ms", elapsedMillis(hashStartedAt));
            return HexFormat.of().formatHex(hash);
        } catch (NoSuchAlgorithmException e) {
            throw new RuntimeException("SHA-256 algorithm not available", e);
        }
    }

    private long elapsedMillis(long startedAt) {
        return (System.nanoTime() - startedAt) / 1_000_000;
    }

}
